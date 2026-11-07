"""
SQLite persistence (`store.Store`) — memory must survive a restart.

Embedding is stubbed out so these run offline and fast; recall quality is a
separate (LLM/embedder) concern, not what persistence needs to prove.
"""
import pytest

from vayl.memory.llm_memory import LLMMemory
from vayl.memory.reconcile import Status
from vayl.storage import store as store_mod
from vayl.storage.store import Store


@pytest.fixture(autouse=True)
def _no_network_embed(monkeypatch):
    # save() embeds new facts; stub it so tests never touch the network.
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.0] for _ in texts])
    # these tests assert on plaintext columns; encryption is exercised separately in test_crypto.py
    monkeypatch.setenv("VAYL_ENCRYPT", "off")


def fact(action="ADD", subject="state", value="Redux", scope="global",
         confidence=0.9, time_ref="present", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": scope,
         "confidence": confidence, "time_ref": time_ref}
    if target_id is not None:
        o["target_id"] = target_id
    return o


def test_roundtrip_preserves_active_facts(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux")
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres")
    Store(db).save("u1", m)

    reloaded = Store(db).load("u1")
    assert sorted(s.value for s in reloaded.active()) == ["Postgres", "Redux"]


def test_superseded_status_survives_reload(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", value="Zustand", target_id=oid), "switched to Zustand")
    Store(db).save("u1", m)

    st = Store(db)
    assert [s.value for s in st.load("u1").active()] == ["Zustand"]
    # history is not in the hot working set anymore — it's read cold, on disk
    assert any(s.value == "Redux" and s.status == Status.SUPERSEDED for s in st.history_rows("u1", "state"))


def test_users_are_isolated(tmp_path):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    a = LLMMemory(); a._apply(fact(value="Redux"), "we use Redux"); st.save("alice", a)
    b = LLMMemory(); b._apply(fact(value="Zustand"), "we use Zustand"); st.save("bob", b)

    assert [s.value for s in st.load("alice").active()] == ["Redux"]
    assert [s.value for s in st.load("bob").active()] == ["Zustand"]
    assert set(st.users()) == {"alice", "bob"}


def test_empty_load_is_empty(tmp_path):
    db = str(tmp_path / "vayl.db")
    assert Store(db).load("nobody").active() == []


# ── hard delete (GDPR erasure — no tombstone, unlike forget/retract) ─────────

def test_delete_hard_erases_a_subject_including_history(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="state", value="Zustand", target_id=oid), "switched")
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres")
    st = Store(db); st.save("u1", m)

    n = st.delete("u1", "state")
    assert n == 2                                   # active + superseded rows both erased
    assert [s.value for s in st.load("u1").active()] == ["Postgres"]
    assert st.history_rows("u1", "state") == []     # nothing left on disk either — no tombstone


def test_delete_all_erases_everything_for_a_user(tmp_path):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    a = LLMMemory(); a._apply(fact(value="Redux"), "we use Redux"); st.save("alice", a)
    b = LLMMemory(); b._apply(fact(value="Zustand"), "we use Zustand"); st.save("bob", b)

    n = st.delete_all("alice")
    assert n == 1
    assert st.load("alice").statements == []
    assert [s.value for s in st.load("bob").active()] == ["Zustand"]   # other users untouched


def test_delete_unknown_subject_returns_zero(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory(); m._apply(fact(value="Redux"), "we use Redux")
    st = Store(db); st.save("u1", m)
    assert st.delete("u1", "nonexistent") == 0


# ── (user_id, agent_id, run_id) memory-space isolation ───────────────────────

def test_agent_spaces_are_isolated_for_the_same_user(tmp_path):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    a = LLMMemory(); a._apply(fact(value="Redux"), "we use Redux"); st.save("u1", a, agent_id="planner")
    b = LLMMemory(); b._apply(fact(value="Zustand"), "we use Zustand"); st.save("u1", b, agent_id="coder")

    assert [s.value for s in st.load("u1", agent_id="planner").active()] == ["Redux"]
    assert [s.value for s in st.load("u1", agent_id="coder").active()] == ["Zustand"]
    assert st.load("u1").active() == []                  # the blank space is separate again


def test_delete_all_no_scope_erases_every_space_but_scoped_erases_one(tmp_path):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    a = LLMMemory(); a._apply(fact(value="Redux"), "x"); st.save("u1", a, agent_id="planner")
    b = LLMMemory(); b._apply(fact(value="Zustand"), "x"); st.save("u1", b, agent_id="coder")

    # scoped: erases only the 'coder' space
    assert st.delete_all("u1", agent_id="coder", run_id="") == 1
    assert [s.value for s in st.load("u1", agent_id="planner").active()] == ["Redux"]

    # unscoped: erases the whole user (all remaining spaces)
    c = LLMMemory(); c._apply(fact(value="Mongo"), "x"); st.save("u1", c, agent_id="dba")
    assert st.delete_all("u1") == 2                       # planner + dba both gone
    assert st.load("u1", agent_id="planner").active() == []


# ── metadata round-trips ─────────────────────────────────────────────────────

def test_metadata_survives_reload(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux")
    m.active()[0].metadata = {"source": "onboarding", "team": "web"}
    Store(db).save("u1", m)

    reloaded = Store(db).load("u1")
    assert reloaded.active()[0].metadata == {"source": "onboarding", "team": "web"}


# ── belief provenance: source + set_at survive reload (F0.1) ──────────────────

def test_source_attribution_survives_reload(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux", source="agent:planner")
    Store(db).save("u1", m)

    reloaded = Store(db).load("u1")
    assert reloaded.active()[0].source == "agent:planner"   # who asserted it is remembered


def test_created_at_is_populated_on_load(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux")
    Store(db).save("u1", m)

    s = Store(db).load("u1").active()[0]
    assert isinstance(s.created_at, float) and s.created_at > 0   # set-at timestamp for staleness/provenance


# ── tenant isolation seam (M1): two tenants on one DB never see each other ────

def test_tenants_are_isolated_on_the_same_db(tmp_path):
    db = str(tmp_path / "vayl.db")
    a = LLMMemory(); a._apply(fact(value="AcmeSecret"), "x"); Store(db, tenant_id="acme").save("u1", a)
    b = LLMMemory(); b._apply(fact(value="GlobexSecret"), "x"); Store(db, tenant_id="globex").save("u1", b)

    # same user_id 'u1', different tenants → fully partitioned
    assert [s.value for s in Store(db, tenant_id="acme").load("u1").active()] == ["AcmeSecret"]
    assert [s.value for s in Store(db, tenant_id="globex").load("u1").active()] == ["GlobexSecret"]
    assert Store(db, tenant_id="default").load("u1").active() == []          # a third tenant sees nothing
    assert Store(db, tenant_id="acme").users() == ["u1"]                     # users() is tenant-scoped too

    # a cross-tenant erasure can't reach into another tenant
    Store(db, tenant_id="acme").delete_all("u1")
    assert Store(db, tenant_id="acme").load("u1").active() == []
    assert [s.value for s in Store(db, tenant_id="globex").load("u1").active()] == ["GlobexSecret"]


def test_default_tenant_is_backward_compatible(tmp_path):
    # a Store with no tenant_id behaves exactly as before (everything lands in 'default')
    db = str(tmp_path / "vayl.db")
    m = LLMMemory(); m._apply(fact(value="Redux"), "x"); Store(db).save("u1", m)
    assert [s.value for s in Store(db).load("u1").active()] == ["Redux"]
    assert [s.value for s in Store(db, tenant_id="default").load("u1").active()] == ["Redux"]


# ── hot-path separation: cost tracks ACTIVE count, not total history ──────────

def _build_history(st, versions):
    """Supersede a single 'state' slot `versions` times → 1 active + (versions-1) history rows."""
    m = LLMMemory()
    m._apply(fact(subject="state", value="v0"), "x")
    st.save("u1", m)
    for i in range(1, versions):
        m = st.load("u1")
        oid = m.active()[0].id
        m._apply(fact(action="SUPERSEDE", subject="state", value=f"v{i}", target_id=oid), "x")
        st.save("u1", m)


def test_load_pulls_only_active_history_stays_on_disk(tmp_path):
    st = Store(str(tmp_path / "vayl.db"))
    _build_history(st, versions=20)                 # 1 active (v19) + 19 superseded

    m = st.load("u1")
    assert [s.value for s in m.active()] == ["v19"]
    assert len(m.statements) == 1                   # ← only the ACTIVE row is in memory, not 20
    # but the full 20-version history is still persisted and cold-readable
    assert len(st.history_rows("u1", "state")) == 20
    assert st.db.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 20


def test_incremental_save_leaves_existing_history_untouched(tmp_path):
    st = Store(str(tmp_path / "vayl.db"))
    _build_history(st, versions=10)
    before = st.db.execute(
        "SELECT id, status, value FROM statements WHERE subject='state' ORDER BY id").fetchall()

    # one more, unrelated write — must NOT rewrite the 10 existing 'state' rows
    m = st.load("u1")
    m._apply(fact(subject="db", value="Postgres"), "x")
    st.save("u1", m)

    after = st.db.execute(
        "SELECT id, status, value FROM statements WHERE subject='state' ORDER BY id").fetchall()
    assert after == before                          # history rows byte-identical: not touched
    assert st.db.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 11   # exactly +1 row


def test_new_supersede_updates_one_row_and_inserts_one(tmp_path):
    st = Store(str(tmp_path / "vayl.db"))
    _build_history(st, versions=5)                   # v4 active, v0..v3 history
    total_before = st.db.execute("SELECT COUNT(*) FROM statements").fetchone()[0]

    m = st.load("u1")                                # loads only v4
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="state", value="v5", target_id=oid), "x")
    st.save("u1", m)

    assert st.db.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == total_before + 1  # +1 insert
    assert [s.value for s in st.load("u1").active()] == ["v5"]
    # the previously-active v4 is now flipped to SUPERSEDED (an UPDATE, not a rewrite)
    hist = {s.value: s.status for s in st.history_rows("u1", "state")}
    assert hist["v4"] == Status.SUPERSEDED and hist["v5"] == Status.ACTIVE


def test_recall_context_never_includes_retired_facts(tmp_path, monkeypatch):
    """The recall fix: recall loads the active-only set, so a retracted/superseded value can
    never reach the LLM context (the qwen 'we use Sentry' silently-wrong failure)."""
    from vayl.memory import llm_memory
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)  # echo the context, no LLM

    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory()
    m._apply(fact(subject="mon", value="Sentry"), "we use Sentry")
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres")
    st.save("u1", m)

    m = st.load("u1")                       # retract Sentry
    m._apply({"action": "RETRACT", "subject": "mon", "value": "Sentry",
              "scope": "global", "confidence": 0.9, "time_ref": "present"}, "dropped Sentry")
    st.save("u1", m)

    context = st.load("u1").query("what tools do we use?")   # recall path: active-only load
    assert "Postgres" in context            # current fact is answerable
    assert "Sentry" not in context          # retired fact can't leak into the answer


# ── graph-erasure completeness (offline, via a fake graph) ───────────────────

class FakeGraph:
    """Records what the engine/store would do to the Neo4j projection — no DB needed."""
    def __init__(self):
        self.edges = []      # (head, relation, tail, ns, subject)
        self.deleted = []    # kwargs passed to delete_edges

    def add_edge(self, head, relation, tail, source="", ns="", subject="", functional=False):
        self.edges.append((head, relation, tail, ns, subject))
        self.last_functional = functional

    def supersede_edge(self, head, relation, new_tail, source="", ns="", subject=""):
        self.edges.append((head, relation, new_tail, ns, subject))

    def retract_edge(self, head, relation, ns=""):
        self.edges.append((head, relation, None, ns, None))

    def retire_subject_edges(self, subject, ns=""):
        self.retired = getattr(self, "retired", [])
        self.retired.append((subject, ns))

    def head_for_subject(self, subject, ns=""):
        return None

    def wipe(self):
        self.edges = []

    def delete_edges(self, ns=None, ns_prefix=None, subject=None):
        self.deleted.append({"ns": ns, "ns_prefix": ns_prefix, "subject": subject})


def _triple_fact(**k):
    o = fact(**{key: v for key, v in k.items() if key in ("action", "subject", "value")})
    o.update({"head": k.get("head"), "relation": k.get("relation"), "tail": k.get("tail")})
    return o


def test_engine_stamps_namespace_on_graph_edges():
    g = FakeGraph()
    m = LLMMemory(graph=g, ns="u1\x1f\x1f")
    m._apply(_triple_fact(subject="alice_employer", value="Acme",
                          head="Alice", relation="works_at", tail="Acme"), "alice works at acme")
    assert g.edges == [("Alice", "works_at", "Acme", "u1\x1f\x1f", "alice_employer")]


def test_delete_all_whole_user_purges_graph_by_prefix(tmp_path):
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)
    m = st.load("u1")
    m._apply(_triple_fact(subject="state", value="Redux",
                          head="team", relation="uses", tail="Redux"), "x")
    st.save("u1", m)
    st.delete_all("u1")                                  # no agent/run -> whole user
    assert {"ns": None, "ns_prefix": "default\x1fu1\x1f", "subject": None} in g.deleted


def test_delete_subject_purges_graph_scoped_to_subject(tmp_path):
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)
    m = st.load("u1")
    m._apply(_triple_fact(subject="state", value="Redux",
                          head="team", relation="uses", tail="Redux"), "x")
    st.save("u1", m)
    st.delete("u1", "state")
    assert {"ns": "default\x1fu1\x1f\x1f", "ns_prefix": None, "subject": "state"} in g.deleted


def test_history_rows_are_embedded_so_they_can_be_found_semantically(tmp_path, monkeypatch):
    """History rows used to be left unembedded — correct while they were unreachable on a read.

    Now that `query(include_history=True)` can reach them, an unembedded row is retrievable only
    by keyword, which makes it second-class in exactly the searches it exists to answer ("what did
    we stop using?"). A retraction tombstone carries the sentence that ended the fact, so it is
    worth finding.
    """
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[1.0] for _ in texts])
    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory()
    m._apply(fact(subject="mon", value="Sentry"), "we use Sentry")
    st.save("u1", m)

    m = st.load("u1")
    m._apply({"action": "RETRACT", "subject": "mon", "value": "Sentry",
              "scope": "global", "confidence": 0.9, "time_ref": "present"}, "dropped Sentry")
    st.save("u1", m)

    rows = st.db.execute("SELECT status, embedding FROM statements WHERE user_id='u1' "
                         "ORDER BY id").fetchall()
    tomb = [emb for status, emb in rows if status == "HISTORICAL"][0]
    assert tomb is not None


def test_a_missing_embedding_is_a_real_sql_null(tmp_path, monkeypatch):
    """The hot-path existence flag is `embedding IS NOT NULL`, so an absent vector must be a real
    NULL — the literal string "null" would satisfy that check and be decoded as garbage."""
    def no_embedder(texts):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(store_mod, "_embed", no_embedder)
    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory()
    m._apply(fact(subject="mon", value="Sentry"), "we use Sentry")
    st.save("u1", m)

    emb = st.db.execute("SELECT embedding FROM statements WHERE user_id='u1'").fetchone()[0]
    assert emb is None


# ── graph projection is REBUILDABLE — the triple is persisted with the fact ───
# Without this the Neo4j projection is an unrecoverable side-store: lose it and the relational
# structure is gone, because head/relation/tail lived only in the extractor's output.

def _triple_o(subject, value, head, rel, tail, action="ADD", target_id=None):
    o = fact(action=action, subject=subject, value=value)
    o.update({"head": head, "relation": rel, "tail": tail})
    if target_id:
        o["target_id"] = target_id
    return o


def test_graph_triple_survives_reload(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = LLMMemory()
    m._apply(_triple_o("bob_employer", "Acme", "Bob", "WORKS_AT", "Acme"), "Bob works at Acme")
    Store(db).save("u1", m)

    s = Store(db).load("u1").active()[0]
    assert (s.head, s.relation, s.tail) == ("Bob", "WORKS_AT", "Acme")


def test_reproject_rebuilds_the_graph_from_the_store(tmp_path):
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)
    m = st.load("u1")
    m._apply(_triple_o("bob_employer", "Acme", "Bob", "WORKS_AT", "Acme"), "x")
    m._apply(_triple_o("acme_owner", "Globex", "Acme", "OWNED_BY", "Globex"), "x")
    st.save("u1", m)

    g.wipe()                                   # lose the graph entirely
    assert st.reproject_graph() == 2
    assert {(h, r, t) for h, r, t, _ns, _sub in g.edges} == {
        ("Bob", "WORKS_AT", "Acme"), ("Acme", "OWNED_BY", "Globex")}


def test_reproject_does_not_resurrect_superseded_facts(tmp_path):
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)
    m = st.load("u1")
    m._apply(_triple_o("bob_employer", "Acme", "Bob", "WORKS_AT", "Acme"), "x")
    oid = m.active()[0].id
    m._apply(_triple_o("bob_employer", "Zenith", "Bob", "WORKS_AT", "Zenith",
                       action="SUPERSEDE", target_id=oid), "x")
    st.save("u1", m)

    g.wipe()
    st.reproject_graph()
    tails = {t for _h, _r, t, _ns, _sub in g.edges}
    assert tails == {"Zenith"}                 # the retired employer must not come back


def test_reproject_is_a_noop_without_a_graph(tmp_path):
    st = Store(str(tmp_path / "vayl.db"))      # no graph attached
    assert st.reproject_graph() == 0


def test_reproject_skips_facts_that_carry_no_triple(tmp_path):
    """Rows written before the triple was persisted (or non-relational facts) are simply skipped."""
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)
    m = st.load("u1")
    m._apply(fact(subject="state", value="Redux"), "we use Redux")   # no head/relation/tail
    st.save("u1", m)

    g.wipe()
    assert st.reproject_graph() == 0 and g.edges == []


def test_id_allocation_is_per_space_not_process_global(tmp_path, monkeypatch):
    """Ids must be minted from each memory's own counter, not a process-global one reassigned on
    every load — that global reassignment was the race the server's coarse lock existed to hide.
    Two memories for different spaces allocate independently; a reload continues from the space's
    MAX; and no shared module-global counter is touched."""
    import itertools

    from vayl.memory import reconcile as rc
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])
    # sabotage the module-global counter — if anything still reads it, ids would come out wrong
    monkeypatch.setattr(rc, "_counter", itertools.count(10 ** 9))

    st = Store(str(tmp_path / "vayl.db"))

    # two independent spaces, each minting from its own counter
    a = st.load("patient_a")
    a._apply(fact(subject="allergy", value="penicillin"), "x")
    a._apply(fact(subject="med", value="warfarin"), "y")
    st.save("patient_a", a)
    b = st.load("patient_b")
    b._apply(fact(subject="allergy", value="latex"), "z")
    st.save("patient_b", b)

    a_ids = sorted(s.id for s in st.load("patient_a").statements)
    b_ids = sorted(s.id for s in st.load("patient_b").statements)
    assert a_ids == [1, 2] and b_ids == [1]          # per-space, both start at 1, none near 1e9

    # a reload continues from the space's MAX, so a new fact never collides with an existing id
    a2 = st.load("patient_a")
    a2._apply(fact(subject="dx", value="pneumonia"), "w")
    st.save("patient_a", a2)
    final = sorted(s.id for s in st.load("patient_a").statements)
    assert final == [1, 2, 3] and len(set(final)) == 3   # continued from MAX(2)+1, no collision


# ── erasure fails closed when the graph projection can't be purged ──

class _Graph:
    """Records delete_edges calls; raises when `down` so we can simulate an unreachable Neo4j."""
    def __init__(self, down=False):
        self.down, self.calls = down, []

    def delete_edges(self, **where):
        self.calls.append(where)
        if self.down:
            raise ConnectionError("neo4j unreachable")


def _store_with(tmp_path, graph):
    st = Store(str(tmp_path / "vayl.db"), graph=graph)
    m = LLMMemory(); m._apply(fact(value="Redux"), "we use Redux"); st.save("alice", m)
    return st


@pytest.mark.parametrize("erase", [lambda st: st.delete("alice", "state"), lambda st: st.delete_all("alice")])
def test_erasure_fails_closed_and_erases_nothing_when_graph_purge_fails(tmp_path, erase):
    st = _store_with(tmp_path, _Graph(down=True))
    with pytest.raises(RuntimeError, match="nothing was erased"):
        erase(st)
    assert [s.value for s in st.load("alice").active()] == ["Redux"]   # store rows untouched


def test_erasure_purges_the_graph_then_the_store(tmp_path):
    g = _Graph()
    st = _store_with(tmp_path, g)
    assert st.delete("alice", "state") == 1
    assert g.calls == [{"ns": st._ns("alice", "", ""), "subject": "state"}]
    assert st.load("alice").statements == []


# ── tenant isolation: policies and the graph namespace ──

def _as_tenant(tenant, fn):
    token = store_mod.bind_tenant(tenant)
    try:
        return fn()
    finally:
        store_mod.reset_tenant(token)


def test_reconcile_policies_are_per_tenant(tmp_path):
    """space_config was keyed without the tenant: globex setting a policy for user u1 overwrote acme's."""
    from vayl.memory.orgmemory import ReconcileMode, ReconcilePolicy
    st = Store(str(tmp_path / "vayl.db"))
    _as_tenant("acme", lambda: st.set_policy("u1", ReconcilePolicy(mode=ReconcileMode.REVIEW)))
    _as_tenant("globex", lambda: st.set_policy("u1", ReconcilePolicy(mode=ReconcileMode.AUTHORITY)))
    assert _as_tenant("acme", lambda: st.get_policy("u1")).mode is ReconcileMode.REVIEW
    assert _as_tenant("globex", lambda: st.get_policy("u1")).mode is ReconcileMode.AUTHORITY


def test_graph_namespace_and_rebuild_are_per_tenant(tmp_path):
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)

    def write(employer):
        m = st.load("u1")
        m._apply(_triple_o("bob_employer", employer, "Bob", "WORKS_AT", employer), "x")
        st.save("u1", m)
    _as_tenant("acme", lambda: write("Acme"))
    _as_tenant("globex", lambda: write("Globex"))
    assert {(t, ns) for _h, _r, t, ns, _s in g.edges} == {
        ("Acme", "acme\x1fu1\x1f\x1f"), ("Globex", "globex\x1fu1\x1f\x1f")}

    # rebuilding one tenant clears only that tenant's namespace — it used to wipe the whole graph
    g.wipe_calls = 0
    g.wipe = lambda: setattr(g, "wipe_calls", g.wipe_calls + 1)
    _as_tenant("acme", st.reproject_graph)
    assert g.wipe_calls == 0 and {"ns": None, "ns_prefix": "acme\x1f", "subject": None} in g.deleted
    # erasing a whole user is scoped to the caller's tenant too
    _as_tenant("acme", lambda: st.delete_all("u1"))
    assert {"ns": None, "ns_prefix": "acme\x1fu1\x1f", "subject": None} in g.deleted


def test_reproject_all_graphs_replays_every_tenant(tmp_path):
    g = FakeGraph()
    st = Store(str(tmp_path / "vayl.db"), graph=g)
    for tenant, employer in (("acme", "Acme"), ("globex", "Globex")):
        def write(employer=employer):
            m = st.load("u1")
            m._apply(_triple_o("bob_employer", employer, "Bob", "WORKS_AT", employer), "x")
            st.save("u1", m)
        _as_tenant(tenant, write)
    g.wipe()
    assert st.reproject_all_graphs() == 2
    assert {ns.split("\x1f")[0] for _h, _r, _t, ns, _s in g.edges} == {"acme", "globex"}


def test_a_retired_fact_records_when_it_stopped_being_current(tmp_path):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    oid = m.active()[0].id
    st.save("u1", m)
    m = st.load("u1")
    m._apply(fact(action="SUPERSEDE", value="Zustand", target_id=oid), "switched to Zustand")
    st.save("u1", m)
    old = next(s for s in st.history_statements("u1") if s.value == "Redux")
    assert old.status is Status.SUPERSEDED and old.metadata.get("retired_at")
    # the current fact, and a past fact recorded as past (never current), carry none
    m = st.load("u1")
    m._apply(fact(subject="framework", value="Angular", time_ref="past"), "we used Angular in 2019")
    st.save("u1", m)
    assert not (st.load("u1").active()[0].metadata or {}).get("retired_at")
    past = [s for s in st.history_statements("u1") if s.value == "Angular"]
    assert past and not (past[0].metadata or {}).get("retired_at")
