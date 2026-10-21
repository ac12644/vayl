"""
Versioned schema migrations — one ordered list for the whole database, recorded in `schema_migrations`.

Every component (Store, Auth, Audit, Decisions, Receipts, Metrics) calls `migrate(db)` on construction,
so whichever opens the database first brings it up to date; the rest find nothing pending.

All pending migrations and their ledger rows commit as ONE transaction, or not at all, on both engines:
Postgres under a cross-process advisory lock, SQLite under `BEGIN IMMEDIATE` (SQLite's own write lock,
which also holds across processes sharing the file). Both engines roll back DDL with the transaction.

Rules for adding a migration — append `(next_version, "name", fn)` to MIGRATIONS, never edit or
reorder a shipped one:
  • Idempotent (IF NOT EXISTS, add_column_if_missing, UPDATE … WHERE not-yet-migrated). The baseline
    has to be, since it upgrades pre-ledger databases in place; tests re-run every migration to check.
  • Additive, so the PREVIOUS release keeps running against the upgraded schema. That is the rollback
    story: roll the code back, the data stays readable. A change that cannot be additive (rename, drop,
    rewrite) must say so in the CHANGELOG and requires a backup before upgrading.
  • An older Vayl refuses to start against a database migrated past what it knows (SchemaTooNew),
    rather than writing rows a newer schema doesn't expect.

Ops: `vayl-migrate status` shows the applied and pending versions; `vayl-migrate up` applies pending
migrations explicitly (e.g. once, before rolling out several server processes).
"""
import datetime
import os
import sys

from vayl.storage.db import Database, ensure


class SchemaTooNew(RuntimeError):
    pass


def _v1_baseline(db):
    """The schema as of 0.4.0, including the in-place upgrades earlier releases applied on startup, so
    a database created by any earlier version lands on the same schema."""
    pk = db.autoincrement_pk()
    # ── memory statements ──
    db.execute("""CREATE TABLE IF NOT EXISTS statements(
        user_id TEXT, id INTEGER, slot TEXT, subject TEXT, value TEXT, scope TEXT,
        status TEXT, supersedes INTEGER, confidence REAL, raw TEXT, seq INTEGER, embedding TEXT,
        agent_id TEXT DEFAULT '', run_id TEXT DEFAULT '', metadata TEXT, subject_hmac TEXT,
        created_at REAL, source TEXT, tenant_id TEXT DEFAULT 'default',
        head TEXT, relation TEXT, tail TEXT)""")
    for ddl in ("embedding TEXT", "agent_id TEXT DEFAULT ''", "run_id TEXT DEFAULT ''",
                "metadata TEXT", "subject_hmac TEXT", "created_at REAL", "source TEXT",
                "tenant_id TEXT DEFAULT 'default'", "head TEXT", "relation TEXT", "tail TEXT"):
        db.add_column_if_missing("statements", ddl)
    # Hot-path indexes (keep load() O(active), independent of history size). `idx_id` (space + id)
    # replaced the old space-only `idx_space`: it serves space lookups AND makes the per-space
    # `SELECT MAX(id)` seed in load() a covering reverse-seek (36ms → 0.01ms at 200k rows).
    db.execute("DROP INDEX IF EXISTS idx_space")
    db.execute("CREATE INDEX IF NOT EXISTS idx_id ON statements(tenant_id, user_id, agent_id, run_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_subj ON statements(tenant_id, user_id, agent_id, run_id, subject_hmac)")
    # `status` in the key lets load()'s `status IN (ACTIVE, FLAGGED_CONFLICT) ORDER BY id` seek straight
    # to the hot rows (93ms → ~3ms at 200k history rows). A partial index would be smaller, but the
    # planner won't match it against the parameterized `IN (?, ?)`.
    db.execute("CREATE INDEX IF NOT EXISTS idx_hot ON statements(tenant_id, user_id, agent_id, run_id, status, id)")
    # per-space reconciliation policy for shared organizational memory
    db.execute("CREATE TABLE IF NOT EXISTS space_config("
               "user_id TEXT, agent_id TEXT, run_id TEXT, policy TEXT, "
               "tenant_id TEXT DEFAULT 'default', PRIMARY KEY(user_id, agent_id, run_id))")
    db.add_column_if_missing("space_config", "tenant_id TEXT DEFAULT 'default'")
    # ── principals (API keys + roles) ──
    db.execute("CREATE TABLE IF NOT EXISTS principals(id TEXT PRIMARY KEY, name TEXT, kind TEXT, "
               "api_key_hash TEXT UNIQUE, roles TEXT, disabled INTEGER DEFAULT 0, created_at REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_prin_key ON principals(api_key_hash)")
    # Added after the first release. Existing rows get NULL: scopes NULL reads as unrestricted and
    # tenant NULL as 'default', so an upgrade never locks an existing principal out.
    db.add_column_if_missing("principals", "scopes TEXT")
    db.add_column_if_missing("principals", "tenant TEXT")
    # ── tamper-evident audit chain ──
    db.execute(f"CREATE TABLE IF NOT EXISTS audit(seq {pk}, "
               "ts TEXT, user_id TEXT, agent_id TEXT, run_id TEXT, action TEXT, detail TEXT, "
               "prev_hash TEXT, entry_hash TEXT, signature TEXT)")
    # pre-chain rows keep NULL hash columns and are treated as legacy
    for ddl in ("prev_hash TEXT", "entry_hash TEXT", "signature TEXT"):
        db.add_column_if_missing("audit", ddl)
    # retention anchor: after a purge, the chain restarts from a SIGNED checkpoint instead of GENESIS
    db.execute("CREATE TABLE IF NOT EXISTS audit_meta(key TEXT PRIMARY KEY, value TEXT, signature TEXT)")
    # ── signed decisions and receipts ──
    db.execute(f"CREATE TABLE IF NOT EXISTS decisions(id {pk}, "
               "ts TEXT, user_id TEXT, agent_id TEXT, run_id TEXT, summary TEXT, snapshot TEXT, "
               "anchor TEXT, entry_hash TEXT, signature TEXT)")
    db.execute(f"CREATE TABLE IF NOT EXISTS receipts(id {pk}, "
               "ts TEXT, kind TEXT, payload TEXT, signature TEXT, public_key TEXT)")
    # ── operational metrics ──
    db.execute("CREATE TABLE IF NOT EXISTS metrics(key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0)")
    # explicit id PK (Postgres has no rowid); on SQLite it aliases rowid, so ordering is unchanged
    db.execute(f"CREATE TABLE IF NOT EXISTS metric_errors(id {pk}, tool TEXT, etype TEXT, emsg TEXT)")


def _v2_policy_per_tenant(db):
    """Key reconcile policies by tenant. space_config's primary key was (user_id, agent_id, run_id), so
    one tenant's set_reconcile_policy overwrote another tenant's for the same user_id. Rebuild it with
    the tenant in the key (neither SQLite nor Postgres can change a primary key in place).

    NOT additive: 0.6.x upserts against the old key and would fail on this table. The version gate is
    what makes that safe: 0.6.x refuses to start on a v2 database (roll back by restoring a backup)."""
    db.execute("CREATE TABLE IF NOT EXISTS space_config_v2("
               "tenant_id TEXT NOT NULL DEFAULT 'default', user_id TEXT, agent_id TEXT, run_id TEXT, "
               "policy TEXT, PRIMARY KEY(tenant_id, user_id, agent_id, run_id))")
    db.execute("INSERT INTO space_config_v2(tenant_id, user_id, agent_id, run_id, policy) "
               "SELECT COALESCE(tenant_id, 'default'), user_id, agent_id, run_id, policy FROM space_config")
    db.execute("DROP TABLE space_config")
    db.execute("ALTER TABLE space_config_v2 RENAME TO space_config")


def _v3_tenant_accountability(db):
    """Stamp decisions, receipts and audit rows with the tenant. Without it, a decision id, receipt id
    or audit_log listing from one tenant was readable by another, and a tenant's retention purge
    deleted every tenant's decisions and receipts.

    Additive: existing rows take the column default 'default', which is where a single-tenant
    deployment's rows belong, and an older Vayl's inserts (which omit the column) land there too.
    On audit the tenant is a filter column only; the hash chain does not cover it."""
    for table in ("decisions", "receipts", "audit"):
        db.add_column_if_missing(table, "tenant_id TEXT DEFAULT 'default'")
    db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_tenant ON decisions(tenant_id, user_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_receipts_tenant ON receipts(tenant_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit(tenant_id, user_id, seq)")


MIGRATIONS = [
    (1, "baseline", _v1_baseline),
    (2, "policy-per-tenant", _v2_policy_per_tenant),
    (3, "tenant-accountability", _v3_tenant_accountability),
]
LATEST = MIGRATIONS[-1][0]
_LOCK = "vayl:schema-migrations"


def _applied(db):
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations("
               "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
    return {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version")}


def _check_not_newer(applied):
    newest = max(applied, default=0)
    if newest > LATEST:
        raise SchemaTooNew(
            f"database schema is at version {newest}, but this Vayl only knows up to {LATEST}. "
            "It was upgraded by a newer Vayl: run that version, or restore the backup taken before "
            "the upgrade.")


def migrate(db):
    """Apply pending migrations in order; refuse a schema newer than this code. Returns the versions
    applied (empty when already current)."""
    db = ensure(db)
    if getattr(db, "_schema_current", False):
        return []
    done = []
    # Postgres: space_lock opens the transaction and takes the advisory lock; a raise rolls it back.
    # SQLite: space_lock is in-process only, so BEGIN IMMEDIATE supplies the cross-process lock and the
    # transaction (Python's sqlite3 would otherwise autocommit each DDL statement on its own).
    with db.space_lock(_LOCK):
        if db.dialect == "sqlite":
            db.commit()                          # BEGIN fails inside an already-open implicit transaction
            db.execute("BEGIN IMMEDIATE")
        try:
            applied = _applied(db)               # read under the lock: another process may have migrated
            _check_not_newer(applied)
            now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
            for version, name, fn in MIGRATIONS:
                if version not in applied:
                    fn(db)
                    db.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)",
                               (version, name, now))
                    done.append(version)
            db.commit()
        except BaseException:
            if db.dialect == "sqlite":
                db.rollback()
            raise
    db._schema_current = True
    return done


def status(db):
    """(applied {version: (name, applied_at)}, pending [(version, name)])."""
    db = ensure(db)
    applied = _applied(db)
    db.commit()
    return applied, [(v, n) for v, n, _ in MIGRATIONS if v not in applied]


def main(argv=None):
    """`vayl-migrate [status|up|reproject-graph]` against VAYL_DATABASE_URL, else VAYL_DB (default
    vayl.db). reproject-graph rebuilds the Neo4j projection for every tenant from the store."""
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd not in ("status", "up", "reproject-graph"):
        print("usage: vayl-migrate [status|up|reproject-graph]", file=sys.stderr)
        return 2
    if cmd == "reproject-graph":
        return _reproject_graph()
    db = Database(os.environ.get("VAYL_DATABASE_URL") or os.path.expanduser(os.environ.get("VAYL_DB", "vayl.db")))
    try:
        if cmd == "up":
            ran = migrate(db)
            print(f"applied: {', '.join(map(str, ran))}" if ran else "nothing to apply")
        applied, pending = status(db)
        _check_not_newer(applied)
    except SchemaTooNew as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for v, (name, at) in applied.items():
        print(f"  v{v}  {name:<20} applied {at}")
    for v, name in pending:
        print(f"  v{v}  {name:<20} PENDING")
    print(f"schema: v{max(applied, default=0)} (this Vayl: v{LATEST})")
    return 0


def _reproject_graph():
    """Wipe the graph and replay every tenant's active facts. Needed once after upgrading to 0.7 with
    the graph enabled: edges written earlier carry a namespace without the tenant, so they are
    neither found by recall_related nor removed by an erasure until they are rebuilt."""
    from vayl.api import mcp_server  # builds the store and attaches the graph from the env
    if mcp_server._store.graph is None:
        print("error: the graph is not enabled. Set VAYL_GRAPH=on and NEO4J_URI / NEO4J_USER / "
              "NEO4J_PASSWORD, the same as for the server.", file=sys.stderr)
        return 1
    print(f"rebuilt the graph: {mcp_server._store.reproject_all_graphs()} edges across all tenants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
