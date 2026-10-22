"""
Database abstraction (M6). The SQLite path and the dialect logic are fully testable here; the live
Postgres path needs a running server (docker-compose `postgres`) and is validated there, not in CI.
"""
import pytest

from vayl.storage.db import Database, _advisory_key, detect_dialect


def test_dialect_detection():
    assert detect_dialect("vayl.db") == "sqlite"
    assert detect_dialect("sqlite:///x.db") == "sqlite"
    assert detect_dialect("postgresql://u:p@h/db") == "postgres"
    assert detect_dialect("postgres://u@h/db") == "postgres"
    assert detect_dialect(None) == "sqlite"


def test_sqlite_execute_roundtrip(tmp_path):
    d = Database(str(tmp_path / "x.db"))
    assert d.dialect == "sqlite"
    d.execute(f"CREATE TABLE t(id {d.autoincrement_pk()}, v TEXT)")
    d.execute("INSERT INTO t(v) VALUES (?)", ("hello",))
    d.commit()
    assert d.execute("SELECT v FROM t WHERE v=?", ("hello",)).fetchone()[0] == "hello"


def test_sqlite_url_form(tmp_path):
    d = Database("sqlite:///" + str(tmp_path / "y.db"))
    d.execute("CREATE TABLE t(v TEXT)"); d.execute("INSERT INTO t VALUES (?)", ("a",)); d.commit()
    assert d.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_placeholder_translation_is_dialect_specific(tmp_path):
    d = Database(str(tmp_path / "z.db"))
    assert d._translate("SELECT ? , ?") == "SELECT ? , ?"          # sqlite: unchanged
    d.dialect = "postgres"
    assert d._translate("SELECT ? , ?") == "SELECT %s , %s"        # postgres: ?→%s


def test_autoincrement_pk_per_dialect(tmp_path):
    d = Database(str(tmp_path / "z.db"))
    assert "AUTOINCREMENT" in d.autoincrement_pk()
    d.dialect = "postgres"
    assert "SERIAL" in d.autoincrement_pk()


def test_advisory_key_is_stable_and_bigint_ranged():
    k1 = _advisory_key("acme/u1//")
    assert k1 == _advisory_key("acme/u1//")                        # stable across calls (not salted)
    assert _advisory_key("a") != _advisory_key("b")
    assert -(2**63) <= k1 < 2**63                                  # fits a Postgres bigint


def test_space_lock_is_a_real_per_key_mutex_on_sqlite(tmp_path):
    d = Database(str(tmp_path / "z.db"))
    with d.space_lock("A"):
        assert d._locks["A"].locked()                              # held inside the context
        assert d._locks.get("B") is None or not d._locks["B"].locked()   # a different space is unaffected
    assert not d._locks["A"].locked()                              # released on exit
    # the same key reuses the same lock object (so concurrent writers to one space actually contend)
    assert d._locks["A"] is d._locks.setdefault("A", object())


def test_add_column_if_missing_tolerates_reruns_but_surfaces_real_errors(tmp_path):
    import sqlite3

    db = Database(str(tmp_path / "m.db"))
    db.execute("CREATE TABLE t(a TEXT)")
    db.add_column_if_missing("t", "b TEXT")
    db.add_column_if_missing("t", "b TEXT")                    # re-run: duplicate column is fine
    with pytest.raises(sqlite3.OperationalError):
        db.add_column_if_missing("no_such_table", "c TEXT")    # a real migration failure is not swallowed


# ── versioned migrations ──

def _cols(d, table):
    return {r[1] for r in d.execute(f"PRAGMA table_info({table})")}


def test_fresh_database_is_created_at_the_latest_version(tmp_path, monkeypatch):
    from vayl.storage import migrations
    from vayl.storage.store import Store
    monkeypatch.setenv("VAYL_ENCRYPT", "off")
    st = Store(str(tmp_path / "v.db"))
    applied, pending = migrations.status(st.db)
    assert list(applied) == [v for v, _n, _f in migrations.MIGRATIONS] and pending == []
    assert migrations.migrate(st.db) == []                    # already current: nothing re-runs
    for t in ("statements", "space_config", "principals", "audit", "audit_meta", "decisions",
              "receipts", "metrics", "metric_errors"):
        assert st.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_pre_ledger_database_is_upgraded_in_place_without_losing_rows(tmp_path):
    """A database written by an early release (no ledger, columns missing) is brought to the baseline
    by the same code path, and its rows survive."""
    from vayl.auth.auth import Auth
    from vayl.storage import migrations
    path = str(tmp_path / "old.db")
    old = Database(path)
    old.execute("CREATE TABLE statements(user_id TEXT, id INTEGER, slot TEXT, subject TEXT, value TEXT, "
                "scope TEXT, status TEXT, supersedes INTEGER, confidence REAL, raw TEXT, seq INTEGER)")
    old.execute("INSERT INTO statements(user_id, id, subject, value, status) VALUES ('u', 1, 's', 'v', 'ACTIVE')")
    old.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, name TEXT, kind TEXT, "
                "api_key_hash TEXT UNIQUE, roles TEXT, disabled INTEGER DEFAULT 0, created_at REAL)")
    old.execute("INSERT INTO principals(id, name, roles) VALUES ('p1', 'ops', '[\"admin\"]')")
    old.execute("CREATE INDEX idx_space ON statements(user_id)")
    old.commit()

    d = Database(path)
    assert migrations.migrate(d) == [v for v, _n, _f in migrations.MIGRATIONS]
    assert {"tenant_id", "subject_hmac", "head", "embedding"} <= _cols(d, "statements")
    assert {"scopes", "tenant"} <= _cols(d, "principals")
    assert d.execute("SELECT value, tenant_id FROM statements").fetchone() == ("v", "default")
    assert "idx_space" not in {r[1] for r in d.execute("PRAGMA index_list(statements)")}
    assert [p["id"] for p in Auth(d).list()] == ["p1"]        # existing principal still listed


def test_older_code_refuses_a_newer_schema(tmp_path):
    from vayl.storage import migrations
    path = str(tmp_path / "v.db")
    d = Database(path)
    migrations.migrate(d)
    d.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, 'from-the-future', 'x')",
              (migrations.LATEST + 1,))
    d.commit()
    with pytest.raises(migrations.SchemaTooNew, match="only knows up to"):
        migrations.migrate(Database(path))


def test_cli_status_and_up(tmp_path, monkeypatch, capsys):
    from vayl.storage import migrations
    monkeypatch.delenv("VAYL_DATABASE_URL", raising=False)
    monkeypatch.setenv("VAYL_DB", str(tmp_path / "cli.db"))
    assert migrations.main(["status"]) == 0 and "PENDING" in capsys.readouterr().out
    assert migrations.main(["up"]) == 0 and "applied: 1" in capsys.readouterr().out
    assert migrations.main(["up"]) == 0 and "nothing to apply" in capsys.readouterr().out
    assert migrations.main(["bogus"]) == 2

    d = Database(str(tmp_path / "cli.db"))
    d.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (99, 'future', 'x')")
    d.commit()
    assert migrations.main(["status"]) == 1 and "only knows up to" in capsys.readouterr().err


def _schema(d):
    return sorted(r for r in d.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


def test_every_migration_is_idempotent(tmp_path):
    """The rule the module states: running a migration again changes nothing."""
    from vayl.storage import migrations
    d = Database(str(tmp_path / "v.db"))
    for _version, _name, fn in migrations.MIGRATIONS:
        fn(d); d.commit()
        before = _schema(d)
        fn(d); d.commit()
        assert _schema(d) == before


def test_a_failing_migration_leaves_nothing_behind(tmp_path, monkeypatch):
    """Pending migrations commit together or not at all: no half-built schema, no ledger row."""
    from vayl.storage import migrations

    def boom(db):
        db.execute("CREATE TABLE half_done(x TEXT)")
        db.add_column_if_missing("statements", "doomed TEXT")
        raise RuntimeError("migration 2 failed midway")

    real = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [*real, (migrations.LATEST + 1, "boom", boom)])
    monkeypatch.setattr(migrations, "LATEST", migrations.LATEST + 1)
    path = str(tmp_path / "v.db")
    with pytest.raises(RuntimeError, match="midway"):
        migrations.migrate(Database(path))
    d = Database(path)
    tables = {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half_done" not in tables and "statements" not in tables       # v1 rolled back with it
    assert "schema_migrations" not in tables or not list(d.execute("SELECT * FROM schema_migrations"))

    monkeypatch.setattr(migrations, "MIGRATIONS", real)
    monkeypatch.setattr(migrations, "LATEST", real[-1][0])
    assert migrations.migrate(Database(path)) == [v for v, _n, _f in real]   # a clean retry succeeds


def test_concurrent_migrators_apply_each_migration_once(tmp_path):
    """Separate connections (as separate processes would have) race to migrate one file."""
    import threading

    from vayl.storage import migrations
    path = str(tmp_path / "v.db")
    ran, errors = [], []

    def worker():
        try:
            ran.extend(migrations.migrate(Database(path)))
        except Exception as e:                                             # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    every = [v for v, _n, _f in migrations.MIGRATIONS]
    assert not errors and ran == every
    assert [r[0] for r in Database(path).execute("SELECT version FROM schema_migrations ORDER BY version")] == every


def test_v2_keeps_existing_policies_and_lets_tenants_share_a_user_id(tmp_path, monkeypatch):
    from vayl.storage import migrations
    path = str(tmp_path / "v.db")
    v1_only = migrations.MIGRATIONS[:1]
    with monkeypatch.context() as mp:                                    # a database still at v1
        mp.setattr(migrations, "MIGRATIONS", v1_only)
        mp.setattr(migrations, "LATEST", 1)
        d = Database(path)
        migrations.migrate(d)
        d.execute("INSERT INTO space_config(user_id, agent_id, run_id, policy, tenant_id) "
                  "VALUES ('u1', '', '', '{\"mode\": \"REVIEW\"}', 'acme')")
        d.commit()
    d = Database(path)
    assert 2 in migrations.migrate(d)
    assert list(d.execute("SELECT tenant_id, user_id, policy FROM space_config")) == [
        ("acme", "u1", '{"mode": "REVIEW"}')]
    d.execute("INSERT INTO space_config(user_id, agent_id, run_id, policy, tenant_id) "
              "VALUES ('u1', '', '', '{\"mode\": \"AUTHORITY\"}', 'globex')")   # was a PK clash
    d.commit()
    assert d.execute("SELECT COUNT(*) FROM space_config").fetchone()[0] == 2


def test_v3_stamps_existing_accountability_rows_with_the_default_tenant(tmp_path, monkeypatch):
    from vayl.storage import migrations
    path = str(tmp_path / "v.db")
    with monkeypatch.context() as mp:                                    # a database still at v2
        mp.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:2])
        mp.setattr(migrations, "LATEST", 2)
        d = Database(path)
        migrations.migrate(d)
        d.execute("INSERT INTO decisions(ts, user_id, summary) VALUES ('t', 'u', 's')")
        d.execute("INSERT INTO receipts(ts, kind, payload) VALUES ('t', 'k', 'p')")
        d.execute("INSERT INTO audit(ts, user_id, action) VALUES ('t', 'u', 'a')")
        d.commit()
    d = Database(path)
    assert 3 in migrations.migrate(d)
    for table in ("decisions", "receipts", "audit"):
        assert list(d.execute(f"SELECT tenant_id FROM {table}")) == [("default",)], table


def test_cli_reproject_graph_refuses_without_a_graph(tmp_path, monkeypatch, capsys):
    from vayl.api import mcp_server
    from vayl.storage import migrations
    monkeypatch.setattr(mcp_server._store, "graph", None)
    assert migrations.main(["reproject-graph"]) == 1
    assert "the graph is not enabled" in capsys.readouterr().err


def test_cli_reproject_graph_rebuilds_every_tenant(monkeypatch, capsys):
    from vayl.api import mcp_server
    from vayl.storage import migrations
    calls = []
    monkeypatch.setattr(mcp_server._store, "graph", object())
    monkeypatch.setattr(mcp_server._store, "reproject_all_graphs", lambda: calls.append(1) or 7)
    assert migrations.main(["reproject-graph"]) == 0 and calls == [1]
    assert "7 edges across all tenants" in capsys.readouterr().out
