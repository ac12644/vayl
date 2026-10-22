"""
The reconciliation engine (_apply): per-action behaviour and the interactions between its guard blocks (composition).
"""

import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import AUTO_THRESHOLD, LLMMemory, is_critical
from vayl.memory.reconcile import Action, Statement, Status
from vayl.memory.schema import from_dict

# ══════════════════════════════════════════════════════════════════
# from test_apply
# ══════════════════════════════════════════════════════════════════

def fact(action="ADD", subject="state", value="Redux", scope="global",
         confidence=0.9, time_ref="present", target_id=None, **extra):
    o = {"action": action, "subject": subject, "value": value, "scope": scope,
         "confidence": confidence, "time_ref": time_ref}
    if target_id is not None:
        o["target_id"] = target_id
    o.update(extra)
    return o


def active_values(m):
    return sorted(s.value for s in m.active())


def test_add_creates_one_active_fact():
    m = LLMMemory()
    act, subj, val = m._apply(fact(value="Redux"), "we use Redux")
    assert act == Action.ADD
    assert active_values(m) == ["Redux"]


def test_multi_entity_scoped_keys_do_not_collide():
    """The multi-entity fix: alice's employer must not overwrite bob's."""
    m = LLMMemory()
    m._apply(fact(subject="alice_employer", value="Acme"), "alice works at Acme")
    m._apply(fact(subject="bob_employer", value="Globex"), "bob works at Globex")
    assert active_values(m) == ["Acme", "Globex"]


def _spy_extractor(monkeypatch, capture):
    def spy(text, active):
        capture["active"] = list(active)
        return []
    monkeypatch.setattr(llm_memory, "llm_extract_classify", spy)


def test_write_passes_all_active_when_space_is_small(monkeypatch):
    cap = {}
    _spy_extractor(monkeypatch, cap)
    m = LLMMemory()
    for i in range(5):
        m.statements.append(Statement(f"s{i}@global", f"s{i}", f"v{i}", "global"))
    m.add("anything")
    assert len(cap["active"]) == 5          # under the cap → the whole active set is shown


def test_write_context_is_capped_for_a_large_space(monkeypatch):
    cap = {}
    _spy_extractor(monkeypatch, cap)
    monkeypatch.setattr(llm_memory, "_RECONCILE_CONTEXT", 10)
    m = LLMMemory()
    for i in range(60):
        m.statements.append(Statement(f"d{i}@global", f"d{i}", f"v{i}", "global", raw=f"database entry {i}"))
    m.add("database update")                # 'database' overlaps all 60 → forces top-k selection
    assert len(cap["active"]) == 10         # bounded, not 60


def test_write_context_keeps_the_relevant_conflict(monkeypatch):
    cap = {}
    _spy_extractor(monkeypatch, cap)
    monkeypatch.setattr(llm_memory, "_RECONCILE_CONTEXT", 10)
    m = LLMMemory()
    for i in range(60):
        m.statements.append(Statement(f"u{i}@global", f"u{i}", f"v{i}", "global", raw=f"unrelated note {i}"))
    m.statements.append(Statement("database@global", "database", "MySQL", "global",
                                  raw="we use MySQL for the database"))
    m.add("the database is now postgres")   # only the database fact overlaps
    subjects = [s.subject for s in cap["active"]]
    assert "database" in subjects and len(subjects) <= 10   # conflict surfaced, context still bounded


def _capture_qa_model(monkeypatch):
    import json
    cap = {}
    def fake_http(req, timeout=30):
        cap["model"] = json.loads(req.data.decode())["model"]
        return {"choices": [{"message": {"content": "ok"}}]}
    monkeypatch.setattr("vayl.memory.llm_client._http_json", fake_http)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
    return cap


def test_read_model_override_is_used_for_synthesis(monkeypatch):
    cap = _capture_qa_model(monkeypatch)
    monkeypatch.setenv("VAYL_READ_MODEL", "gpt-4.1-nano")
    llm_memory._qa("some facts", "a question?")
    assert cap["model"] == "gpt-4.1-nano"        # read model, not the gpt-5-mini extraction model


def test_read_defaults_to_the_extraction_model(monkeypatch):
    cap = _capture_qa_model(monkeypatch)
    monkeypatch.delenv("VAYL_READ_MODEL", raising=False)
    llm_memory._qa("some facts", "a question?")
    assert cap["model"] == "gpt-5-mini"          # unset → same model as extraction


def test_recall_skips_query_embedding_for_a_small_memory(monkeypatch):
    calls = {"embed": 0}
    monkeypatch.setattr("vayl.memory.llm_client._embed", lambda texts: calls.__setitem__("embed", calls["embed"] + 1) or [[0.0] for _ in texts])
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: ctx)
    m = LLMMemory()
    for i in range(20):
        m.statements.append(Statement(f"s{i}@global", f"s{i}", f"v{i}", "global"))
    m.query("anything")                     # 20 <= cap(40) → all facts passed, no embed
    assert calls["embed"] == 0


def test_recall_embeds_to_bound_a_large_memory(monkeypatch):
    calls = {"embed": 0}
    monkeypatch.setattr("vayl.memory.llm_client._embed", lambda texts: calls.__setitem__("embed", calls["embed"] + 1) or [[0.0] for _ in texts])
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: ctx)
    monkeypatch.setattr(llm_memory, "_RECALL_CONTEXT", 10)
    m = LLMMemory()
    for i in range(30):
        s = Statement(f"s{i}@global", f"s{i}", f"v{i}", "global", raw=f"note {i}")
        s._emb = [float(i)]
        m.statements.append(s)
    m.query("something")                    # 30 > cap(10) → embeds to rank down to top-k
    assert calls["embed"] >= 1


def test_removal_mislabelled_as_add_is_upgraded_to_retract():
    """'ServiceA no longer calls ServiceB' came back as ADD in the live graph benchmark."""
    m = LLMMemory()
    m._apply(fact(subject="calls", value="ServiceB"), "ServiceA calls ServiceB")
    act, _s, _v = m._apply(fact(action="ADD", subject="calls", value="ServiceB"),
                           "ServiceA no longer calls ServiceB")
    assert act == Action.RETRACT
    assert active_values(m) == []                     # slot is empty, not holding the stale value


def test_retract_upgrade_fires_for_unspecified_values():
    m = LLMMemory()
    m._apply(fact(subject="monitoring", value="Datadog"), "we monitor with Datadog")
    act, _s, _v = m._apply(fact(action="ADD", subject="monitoring", value="(unspecified)"),
                           "we dropped the vendor; Datadog is gone")
    assert act == Action.RETRACT and active_values(m) == []


def test_a_hedged_removal_never_retracts():
    """The over-deletion failure: 'considering dropping X' must keep X."""
    m = LLMMemory()
    m._apply(fact(subject="cache", value="Redis"), "we use Redis")
    act, _s, _v = m._apply(fact(action="ADD", subject="cache", value="Redis"),
                           "we are considering dropping Redis")
    assert act != Action.RETRACT
    assert active_values(m) == ["Redis"]              # still true


def test_a_replacement_stays_a_supersede_not_a_retract():
    """'moved off Sentry to Rollbar' names a NEW value → supersede, not removal."""
    m = LLMMemory()
    m._apply(fact(subject="monitoring", value="Sentry"), "we use Sentry")
    act, _s, _v = m._apply(fact(action="SUPERSEDE", subject="monitoring", value="Rollbar"),
                           "we no longer use Sentry — we moved to Rollbar")
    assert act == Action.SUPERSEDE
    assert active_values(m) == ["Rollbar"]


def test_sarcastic_removal_is_not_retracted():
    m = LLMMemory()
    m._apply(fact(subject="cache", value="Redis"), "we use Redis")
    act, _s, _v = m._apply(fact(action="ADD", subject="cache", value="Redis"),
                           "oh sure, we dropped Redis 🙄")
    assert act != Action.RETRACT and active_values(m) == ["Redis"]


class _Graph:
    """Fake graph that models edge validity exactly like the real Cypher (functional retire + MERGE)."""
    def __init__(self):
        self.edges = []   # {head, rel, tail, ns, subject, valid}

    def add_edge(self, head, relation, tail, source="", ns="", subject="", functional=False):
        if functional:
            for e in self.edges:
                if e["head"] == head and e["rel"] == relation and e["ns"] == ns and e["tail"] != tail:
                    e["valid"] = False
        for e in self.edges:
            if e["head"] == head and e["rel"] == relation and e["ns"] == ns and e["tail"] == tail:
                e["valid"] = True; e["subject"] = subject; return
        self.edges.append(dict(head=head, rel=relation, tail=tail, ns=ns, subject=subject, valid=True))

    def supersede_edge(self, head, relation, new_tail, source="", ns="", subject=""):
        for e in self.edges:
            if e["head"] == head and e["rel"] == relation and e["ns"] == ns:
                e["valid"] = False
        self.add_edge(head, relation, new_tail, source, ns, subject)

    def retract_edge(self, head, relation, ns=""):
        for e in self.edges:
            if e["head"] == head and e["rel"] == relation and e["ns"] == ns:
                e["valid"] = False

    def retire_subject_edges(self, subject, ns=""):
        for e in self.edges:
            if e["subject"] == subject and e["ns"] == ns:
                e["valid"] = False

    def head_for_subject(self, subject, ns=""):
        for e in self.edges:
            if e["subject"] == subject and e["ns"] == ns:
                return e["head"]
        return None

    def valid(self):
        return sorted((e["head"], e["rel"], e["tail"]) for e in self.edges if e["valid"])


def _triple(action, subject, value, head, relation, tail):
    o = fact(action=action, subject=subject, value=value)
    o.update({"head": head, "relation": relation, "tail": tail})
    return o


def test_graph_functional_repoint_retires_old_edge_even_when_labelled_add():
    """The graph gap: routes_to change comes back as ADD with a DIFFERENT subject, so the slot
    invariant can't fire — the graph invariant must still retire the old edge."""
    g = _Graph()
    m = LLMMemory(graph=g)
    m._apply(_triple("ADD", "gw_route_a", "service-v1", "API gateway", "routes_to", "service-v1"), "routes to v1")
    m._apply(_triple("ADD", "gw_route_b", "service-v2", "API gateway", "routes_to", "service-v2"), "now v2")
    assert g.valid() == [("API gateway", "routes_to", "service-v2")]   # v1 retired, not left live


def test_graph_repoint_with_inconsistent_head_anchors_to_the_canonical_entity():
    """The live benchmark failure: the slot supersedes correctly, but the superseding fact names a
    DIFFERENT head ('Org' vs 'API Gateway'). Two guards apply — subject-keyed retirement removes the
    stale v1, and entity resolution anchors the new edge back onto the head this slot already uses,
    so a question about 'API Gateway' can actually reach v2 (previously it stranded on 'Org' and the
    query degraded to "I don't know")."""
    g = _Graph()
    m = LLMMemory(graph=g)
    m._apply(_triple("ADD", "gw_route", "service-v1", "API Gateway", "routes_to", "service-v1"), "routes to v1")
    m._apply(_triple("SUPERSEDE", "gw_route", "service-v2", "Org", "routes_to", "service-v2"), "now v2, not v1")
    assert g.valid() == [("API Gateway", "routes_to", "service-v2")]     # anchored, stale v1 retired
    assert not any(h == "Org" for h, _r, _t in g.valid())                # no orphan node


def test_graph_multivalued_relation_keeps_all_tails():
    """depends_on is additive — a second dependency must NOT retire the first."""
    g = _Graph()
    m = LLMMemory(graph=g)
    m._apply(_triple("ADD", "auth_dep_1", "token service", "auth", "depends_on", "token service"), "x")
    m._apply(_triple("ADD", "auth_dep_2", "Redis", "auth", "depends_on", "Redis"), "x")
    assert g.valid() == [("auth", "depends_on", "Redis"), ("auth", "depends_on", "token service")]


def test_graph_coexist_never_retires():
    """An explicit COEXIST keeps both edges regardless of relation type."""
    g = _Graph()
    m = LLMMemory(graph=g)
    m._apply(_triple("ADD", "route_web", "v1", "gw", "routes_to", "v1"), "x")
    m._apply(_triple("COEXIST", "route_mobile", "v2", "gw", "routes_to", "v2"), "x")
    assert ("gw", "routes_to", "v1") in g.valid() and ("gw", "routes_to", "v2") in g.valid()


class _NsGraph:
    """Fake graph modelling ns-scoped reads, to prove graph_query stays within one tenant."""
    def __init__(self):
        self.edges = []   # (head, rel, tail, ns, valid)

    def seed(self, head, rel, tail, ns):
        self.edges.append((head, rel, tail, ns, True))

    def vector_search(self, qvec, k=15, ns=None):
        return [(h, r, t) for (h, r, t, e, v) in self.edges if v and (ns is None or e == ns)][:k]

    def search_entities(self, text, limit=8):
        ql = text.lower()
        names = {h for (h, *_ ) in self.edges} | {e[2] for e in self.edges}
        return [n for n in names if n.lower() in ql][:limit]

    def all_edges(self, valid_only=True, limit=1000, ns=None):
        return [(h, r, t, v) for (h, r, t, e, v) in self.edges
                if (not valid_only or v) and (ns is None or e == ns)][:limit]

    def neighborhood(self, entities, hops=2, valid_only=True, limit=800, ns=None):
        names = {x.lower() for x in entities}
        return [(h, r, t, v) for (h, r, t, e, v) in self.edges
                if (h.lower() in names or t.lower() in names) and (not valid_only or v)
                and (ns is None or e == ns)][:limit]


def test_graph_query_is_scoped_to_the_tenant(monkeypatch):
    monkeypatch.setattr("vayl.memory.llm_client._embed", lambda texts: [[0.0] for _ in texts])
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: ctx)   # surface the retrieved context
    g = _NsGraph()
    g.seed("Bob", "WORKS_AT", "Acme", "tenantA\x1f\x1f")
    g.seed("Bob", "WORKS_AT", "EvilCorp", "tenantB\x1f\x1f")     # another tenant, same entity name
    m = LLMMemory(graph=g, ns="tenantA\x1f\x1f")
    _answer, _seeds, triples = m.graph_query("who does Bob work for?")
    assert ("Bob", "WORKS_AT", "Acme") in triples
    assert ("Bob", "WORKS_AT", "EvilCorp") not in triples        # tenant B never leaks in


def test_add_that_collides_supersedes_instead_of_leaving_two_active():
    """The benchmark repro: 'Redux' then 'Zustand' come back as ADD on the same slot."""
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    old_id = m.active()[0].id
    act, _, _ = m._apply(fact(action="ADD", value="Zustand"), "we switched to Zustand")
    assert act == Action.SUPERSEDE                      # ADD-collision is resolved to a supersede
    assert active_values(m) == ["Zustand"]             # never ['Redux', 'Zustand']
    assert next(s for s in m.statements if s.id == old_id).status == Status.SUPERSEDED
    assert m.active()[0].supersedes == old_id           # provenance chain intact


def test_supersede_without_target_id_still_retires_the_old_value():
    """SUPERSEDE with a null/unlinked target_id must fall back to the same-slot rival."""
    m = LLMMemory()
    m._apply(fact(value="Sentry", subject="error_monitoring"), "we use Sentry")
    act, _, _ = m._apply(fact(action="SUPERSEDE", subject="error_monitoring", value="Bugsnag"),
                         "we moved to Bugsnag")           # no target_id
    assert act == Action.SUPERSEDE
    assert active_values(m) == ["Bugsnag"]


def test_collision_is_scoped_so_coexistence_survives():
    """Different scopes are NOT a collision — web/mobile both stay true."""
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux", scope="web"), "web uses Redux")
    m._apply(fact(subject="state", value="Zustand", scope="mobile"), "mobile uses Zustand")
    assert active_values(m) == ["Redux", "Zustand"]     # invariant is per (subject, scope)


def test_same_value_re_add_does_not_spuriously_supersede():
    """Re-asserting the SAME value isn't a contradiction — no phantom supersede."""
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    first_id = m.active()[0].id
    m._apply(fact(action="ADD", value="Redux"), "still on Redux")
    assert next(s for s in m.statements if s.id == first_id).status == Status.ACTIVE


def test_collision_respects_source_authority_policy():
    """A lower-authority ADD collision must NOT overwrite — current stands, incoming is flagged."""
    from vayl.memory.orgmemory import ReconcileMode, ReconcilePolicy
    m = LLMMemory(policy=ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3, "analyst": 1}))
    m._apply(fact(subject="q3_revenue", value="10M"), "revenue is 10M", source="cfo")
    act, _, _ = m._apply(fact(action="ADD", subject="q3_revenue", value="8M"),
                         "revenue is 8M", source="analyst")
    assert act == Action.FLAG
    assert active_values(m) == ["10M"]                  # authoritative value stands, not silently overwritten


def test_supersede_retires_the_old_value():
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    old_id = m.active()[0].id
    act, _, _ = m._apply(fact(action="SUPERSEDE", value="Zustand", target_id=old_id),
                         "we switched to Zustand")
    assert act == Action.SUPERSEDE
    assert active_values(m) == ["Zustand"]          # old is gone from active
    old = next(s for s in m.statements if s.id == old_id)
    assert old.status == Status.SUPERSEDED
    new = next(s for s in m.active())
    assert new.supersedes == old_id                 # provenance chain intact


def test_low_confidence_supersede_flags_instead_of_guessing():
    """Honest-uncertainty gate: a resolving action below threshold must FLAG."""
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    old_id = m.active()[0].id
    act, _, _ = m._apply(
        fact(action="SUPERSEDE", value="Zustand", confidence=AUTO_THRESHOLD - 0.2, target_id=old_id),
        "maybe Zustand?")
    assert act == Action.FLAG
    old = next(s for s in m.statements if s.id == old_id)
    assert old.status == Status.ACTIVE              # NOT retired on a guess


def test_retract_empties_the_slot():
    m = LLMMemory()
    m._apply(fact(subject="monitoring", value="Sentry"), "we use Sentry")
    act, _, _ = m._apply(fact(action="RETRACT", subject="monitoring", value="Sentry"),
                         "we dropped Sentry")
    assert act == Action.RETRACT
    assert active_values(m) == []                   # slot is empty, not left stale
    # a tombstone is kept for provenance/audit
    assert any(s.status == Status.HISTORICAL and "retracted" in s.value for s in m.statements)


def test_low_confidence_retract_flags_not_deletes():
    m = LLMMemory()
    m._apply(fact(subject="monitoring", value="Sentry"), "we use Sentry")
    act, _, _ = m._apply(
        fact(action="RETRACT", subject="monitoring", value="Sentry", confidence=AUTO_THRESHOLD - 0.2),
        "we might drop Sentry")
    assert act == Action.FLAG
    assert "Sentry" in active_values(m)             # not deleted on a maybe


def test_retract_nothing_to_remove_is_skip():
    m = LLMMemory()
    act, _, _ = m._apply(fact(action="RETRACT", subject="ghost", value="x"), "drop the thing")
    assert act == Action.SKIP


def test_forget_forces_retraction_even_when_extractor_says_add(monkeypatch):
    """The `forget` bug fix: an explicit forget must retire the fact even if the
    extractor misclassifies it as ADD/SUPERSEDE."""
    m = LLMMemory()
    m._apply(fact(subject="monitoring", value="Sentry"), "we use Sentry")

    # Extractor deliberately returns a NON-retract action; forget must override it.
    monkeypatch.setattr(llm_memory, "llm_extract_classify", lambda text, active: [
        {"action": "ADD", "subject": "monitoring", "value": "Sentry",
         "scope": "global", "confidence": 0.9, "time_ref": "present"}])

    (act, _, _), = m.forget("stop tracking Sentry")
    assert act == Action.RETRACT
    assert active_values(m) == []                    # actually retired, not re-added


def test_past_statement_is_history_never_overwrites_present():
    """"Historically we billed monthly" must not overwrite "we now bill annually"."""
    m = LLMMemory()
    m._apply(fact(subject="billing", value="annual"), "we bill annually now")
    act, _, _ = m._apply(fact(subject="billing", value="monthly", time_ref="past"),
                         "historically we billed monthly")
    assert act == Action.ARCHIVE
    assert active_values(m) == ["annual"]           # present truth untouched
    assert any(s.value == "monthly" and s.status == Status.HISTORICAL for s in m.statements)


def test_future_statement_is_flagged_not_asserted():
    m = LLMMemory()
    act, _, _ = m._apply(fact(subject="db", value="Postgres", time_ref="future"),
                         "we'll move to Postgres next quarter")
    assert act == Action.FLAG                        # pending, not current truth


@pytest.mark.parametrize("action", ["SKIP", "DEDUP"])
def test_skip_and_dedup_store_nothing(action):
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    before = len(m.statements)
    m._apply(fact(action=action, value="Redux"), "yeah, Redux")
    assert len(m.statements) == before


def test_flag_creates_a_flagged_statement():
    m = LLMMemory()
    act, _, _ = m._apply(fact(action="FLAG", value="Redux"), "unclear")
    assert act == Action.FLAG
    assert any(s.status == Status.FLAGGED for s in m.statements)


def test_refine_updates_the_target_value():
    m = LLMMemory()
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres")
    tid = m.active()[0].id
    m._apply(fact(action="REFINE", subject="db", value="Postgres 16 on RDS", target_id=tid),
             "Postgres 16 on RDS")
    assert active_values(m) == ["Postgres 16 on RDS"]


def test_get_returns_statement_by_id_or_none():
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    mid = m.active()[0].id
    assert m.get(mid).value == "Redux"
    assert m.get(999999) is None


def test_update_is_audit_preserving():
    """Editing by id retires the old value to history and makes the new one active —
    the old value is NOT silently overwritten."""
    m = LLMMemory()
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres")
    mid = m.active()[0].id
    new = m.update(mid, "MySQL")
    assert active_values(m) == ["MySQL"]                 # new value is current
    assert new.supersedes == mid                         # provenance chain
    old = m.get(mid)
    assert old.status == Status.SUPERSEDED and old.value == "Postgres"   # old kept


def test_update_unknown_id_returns_none():
    m = LLMMemory()
    assert m.update(123456, "x") is None


def test_history_returns_full_ordered_chain():
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="state", value="Zustand", target_id=oid), "switched")
    chain = m.history("state")
    assert [s.value for s in chain] == ["Redux", "Zustand"]       # oldest → newest
    assert chain[0].status == Status.SUPERSEDED and chain[1].status == Status.ACTIVE


def test_apply_stamps_source_on_created_facts():
    # add()/forget() thread `source` straight into _apply, the deterministic seam we assert on here
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux", source="agent:planner")
    assert m.active()[0].source == "agent:planner"


def test_supersede_records_source_of_the_new_assertion():
    m = LLMMemory()
    m._apply(fact(subject="state", value="Redux"), "we use Redux", source="alice")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="state", value="Zustand", target_id=oid),
             "switched to Zustand", source="bob")
    new = m.active()[0]
    assert new.value == "Zustand" and new.source == "bob"     # the change is attributed to bob


def test_query_with_provenance_returns_exact_facts_used(monkeypatch):
    """Provenance must cite the ACTUAL facts placed in the model's context — the basis for
    'why did the agent do X?' and the safety gate."""
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: "answer")

    m = LLMMemory()
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres", source="onboarding")
    answer, used = m.query("what database?", with_provenance=True)
    assert answer == "answer"
    assert len(used) == 1
    p = used[0]
    assert p["subject"] == "db" and p["value"] == "Postgres"
    assert p["source"] == "onboarding" and p["status"] == "ACTIVE"
    assert "confidence" in p and "supersedes" in p and "set_at" in p


def test_query_without_provenance_stays_backward_compatible(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: "just-the-answer")
    m = LLMMemory()
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres")
    assert m.query("what database?") == "just-the-answer"     # unchanged single-value return


def test_view_partitions_by_status():
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", value="Zustand", target_id=oid), "switched to Zustand")
    m._apply(fact(action="FLAG", subject="db", value="Mongo"), "db unclear")
    act, flg, sup, hist = m.view()
    assert [s.value for s in act] == ["Zustand"]
    assert [s.value for s in flg] == ["Mongo"]
    assert [s.value for s in sup] == ["Redux"]


def test_restating_a_fact_does_not_duplicate_the_active_row():
    """A reconciling store must not accumulate identical actives when a fact is simply repeated."""
    m = LLMMemory()
    m._apply(fact(subject="cache", value="Redis"), "we use Redis")
    act, _s, _v = m._apply(fact(subject="cache", value="Redis"), "we use Redis")
    assert act == Action.DEDUP
    assert active_values(m) == ["Redis"]        # one row, not two


# ══════════════════════════════════════════════════════════════════
# from test_apply_composition
# ══════════════════════════════════════════════════════════════════

def o(subject, value, action="ADD", kind="state", conf=0.9, time_ref="present", target_id=None,
      reason=None):
    d = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": conf, "time_ref": time_ref, "kind": kind}
    if target_id is not None:
        d["target_id"] = target_id
    if reason is not None:
        d["reason"] = reason
    return d


@pytest.fixture
def clinical(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", from_dict({"slots": [
        {"name": "active_medication", "category": "critical", "confirm": True,
         "aliases": ["meds"]},
        {"name": "allergy", "category": "critical"},
    ]}))


def test_event_exemption_beats_the_retract_upgrade(clinical):
    """event + RETRACT-UPGRADE: removal language in a sentence must not retract an event. The event
    branch runs first and strips SUPERSEDE/RETRACT/REFINE to ADD; the retract upgrade is then gated
    on `not is_event`."""
    m = LLMMemory()
    m._apply(o("alice_race", "5k", kind="event"), "ran a 5k")
    m._apply(o("alice_race", "10k", kind="event"), "we dropped the 5k and no longer run it")
    assert len(m.active()) == 2                              # both events survive


def test_an_event_on_a_confirm_slot_is_not_gated(clinical):
    """event × CONFIRMATION GATE: an event cannot supersede, so there is nothing hazardous to
    confirm — it must store immediately, not sit in the review queue."""
    m = LLMMemory()
    act, _, _ = m._apply(o("active_medication", "gave a dose at 09:00", kind="event"),
                         "administered a dose")
    assert act is not Action.FLAG and not m.pending()


def test_past_restatement_dedups_but_a_distinct_past_fact_archives(clinical):
    """VALID-TIME GATE × RESTATEMENT: the gate returns before the restatement check, so it carries
    its own dedup. Same past fact twice -> one row; different past fact -> a second row."""
    m = LLMMemory()
    m._apply(o("stack", "mysql", time_ref="past"), "we used mysql")
    m._apply(o("stack", "mysql", time_ref="past"), "yeah, mysql back then")
    m._apply(o("stack", "oracle", time_ref="past"), "before that, oracle")
    hist = [s for s in m.statements if s.status is Status.HISTORICAL]
    assert len(hist) == 2


def test_mislabelled_retract_on_a_confirm_slot_is_proposed_not_applied(clinical):
    """RETRACT-UPGRADE × CONFIRMATION GATE: a definite 'stopped the warfarin' arrives as ADD, is
    upgraded to RETRACT, and must then hit the confirmation gate — a removal on a confirm slot is
    proposed, the value stands. Both guards firing in sequence, on one statement.

    Note the wording is UNHEDGED. 'we should stop the warfarin' would be caught earlier as
    hypothetical ('should') and never upgraded — correctly, since a suggestion is not an order. The
    composition here is only reached by definite removal language."""
    m = LLMMemory()
    m._apply(o("active_medication", "warfarin"), "on warfarin")
    act, _, _ = m._apply(o("active_medication", "warfarin"), "stopped the warfarin, no longer taking it")
    assert act is Action.FLAG
    assert [s.value for s in m.active()] == ["warfarin"]     # untouched
    assert m.pending() and (m.pending()[0].metadata or {}).get("pending") == "RETRACT"


def test_a_hedged_removal_on_a_confirm_slot_does_not_even_propose(clinical):
    """The conservative counterpart the failing draft of the test above revealed: hedged removal
    language ('should stop') is caught as hypothetical before the retract upgrade, so it neither
    retracts nor queues a proposal — a suggestion in conversation must not become a review item."""
    m = LLMMemory()
    m._apply(o("active_medication", "warfarin"), "on warfarin")
    m._apply(o("active_medication", "warfarin"), "we should probably stop the warfarin at some point")
    assert [s.value for s in m.active()] == ["warfarin"]
    assert not m.pending()


def test_repeated_proposal_does_not_stack_in_the_queue(clinical):
    """CONFIRMATION GATE × itself: the same change proposed twice is one decision. This is the
    duplicate-pending bug — an early-return guard that did not dedup."""
    m = LLMMemory()
    m._apply(o("active_medication", "warfarin"), "on warfarin")
    for _ in range(3):
        m._apply(o("meds", "apixaban", action="SUPERSEDE"), "switch to apixaban")
    assert len(m.pending()) == 1
    # a genuinely different proposal still queues
    m._apply(o("meds", "rivaroxaban", action="SUPERSEDE"), "actually rivaroxaban")
    assert len(m.pending()) == 2


def test_category_survives_a_supersede(clinical):
    """SCHEMA CATEGORY × SAME-SLOT INVARIANT: a declared category must stay on the fact after it
    supersedes a rival, or the critical-fact channel stops seeing it after the first update."""
    m = LLMMemory()
    m._apply(o("allergy", "penicillin"), "allergic to penicillin")
    m._apply(o("allergy", "penicillin and latex"), "also latex now")
    live = m.active()[0]
    assert live.value == "penicillin and latex"
    assert is_critical(live, categories=["critical"])       # still tagged after the supersede


def test_flag_reason_and_category_coexist(clinical, monkeypatch):
    """CATEGORY × FLAG-REASON: a flagged fact on a categorised slot keeps both its reason and its
    category — two separate metadata writes late in _apply."""
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", from_dict({"slots": [
        {"name": "allergy", "category": "critical"}]}))
    m = LLMMemory()
    act, _, _ = m._apply(o("allergy", "shellfish", action="FLAG", conf=0.9,
                           reason="unclear if current"), "maybe allergic to shellfish?")
    s = next(x for x in m.statements if x.status is Status.FLAGGED)
    meta = s.metadata or {}
    assert meta.get("reason") == "unclear if current" and meta.get("category") == "critical"


def test_fragment_resolution_does_not_touch_declared_slots(clinical, monkeypatch):
    """FRAGMENT RESOLUTION × SCHEMA: the fold only runs for spec is None, so a declared slot is
    canonicalised by the schema, never second-guessed by the fold."""
    monkeypatch.setattr(llm_memory, "_SLOT_RESOLVE", True)
    m = LLMMemory()
    m._apply(o("active_medication", "warfarin"), "on warfarin")
    # a near-similar subject that also resolves to the declared slot: schema handles it, and because
    # it is a confirm slot the second same-value write dedups rather than proposing a no-op change
    act, subj, _ = m._apply(o("meds", "warfarin"), "still on warfarin")
    assert subj == "active_medication"                       # schema canonicalised, fold skipped


def test_fragment_fold_requires_state_on_both_sides(monkeypatch):
    """FRAGMENT RESOLUTION × EVENT: an incoming event never folds, and an existing event is never a
    fold target — either direction would destroy an event."""
    monkeypatch.setattr(llm_memory, "_SLOT_RESOLVE", True)
    m = LLMMemory()
    m._apply(o("race_report", "5k", kind="event"), "ran a 5k")
    act, _, _ = m._apply(o("race_report_may", "5k", kind="event"), "another 5k")
    assert len(m.active()) == 2 and act is not Action.DEDUP


def test_update_by_id_bypasses_the_confirmation_gate(clinical):
    """update() is a human editing a specific fact by id (the update_memory tool), which IS
    the human decision the confirmation gate waits for — so it applies directly, unlike the LLM
    reading conversational text. Pinning this so the gate is not later wired into update() by
    mistake."""
    m = LLMMemory()
    m._apply(o("active_medication", "warfarin"), "on warfarin")
    mid = m.active()[0].id
    new = m.update(mid, "apixaban", source="dr_smith")
    assert new is not None and new.value == "apixaban"
    assert [s.value for s in m.active()] == ["apixaban"]     # applied, not queued
    assert not m.pending()


def test_coexist_on_the_same_scope_cannot_leave_two_active_values():
    """COEXIST means 'differs by scope'. A model that says COEXIST but gives the SAME scope left
    Redux and Zustand both active — the stale-value failure the invariant exists to prevent."""
    m = LLMMemory()
    m._apply(fact(value="Redux"), "we use Redux")
    act, _s, _v = m._apply(fact(action="COEXIST", value="Zustand"), "we use Zustand")
    assert act.value == "SUPERSEDE"
    assert [s.value for s in m.active()] == ["Zustand"]


def test_a_real_coexist_on_a_different_scope_keeps_both():
    m = LLMMemory()
    m._apply(fact(value="Redux", scope="web"), "the web app uses Redux")
    act, _s, _v = m._apply(fact(action="COEXIST", value="MobX", scope="mobile"), "mobile uses MobX")
    assert act.value == "COEXIST"
    assert sorted(s.value for s in m.active()) == ["MobX", "Redux"]


def test_check_reports_each_reason_once():
    """Two unresolved proposals on one subject are one 'disputed' reason, not two identical lines."""
    m = LLMMemory()
    m._apply(fact(value="Redux"), "x")
    m._apply(fact(action="FLAG", value="Zustand"), "maybe zustand")
    m._apply(fact(action="FLAG", value="MobX"), "maybe mobx")
    reasons = m.check("state")["reasons"]
    assert len(reasons) == len(set(reasons)) and any("disputed" in r for r in reasons)
