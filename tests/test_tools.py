"""
The MCP tool layer, end to end — the product's public contract.

Engine and store logic are tested on their own elsewhere; these drive the real `@mcp.tool` functions
through authorization, the space lock, load → reconcile → save, the audit chain, receipts, and the
text a client actually receives. Only the model seams are stubbed (extraction, answering, embedding),
so every test is offline and deterministic.
"""
import json
import os
import tempfile
import uuid

import pytest

os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
from vayl.api import mcp_server as s  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402
from vayl.storage import store as store_mod  # noqa: E402


def _fact(action, subject, value):
    return {"action": action, "subject": subject, "value": value, "scope": "global",
            "confidence": 0.95, "time_ref": "present"}


# What a model would extract from each sentence the tests send.
SCRIPT = {
    "We use Redux for state.": [_fact("ADD", "state", "Redux")],
    "We moved off Redux to Zustand.": [_fact("SUPERSEDE", "state", "Zustand")],
    "We use Sentry for monitoring.": [_fact("ADD", "monitoring", "Sentry")],
    "We dropped Sentry.": [_fact("RETRACT", "monitoring", "Sentry")],
    "If we used Vue it would be faster.": [_fact("SKIP", "framework", "Vue")],
    "Patient is full code.": [_fact("ADD", "code_status", "full code")],
    "Remove the code status.": [_fact("RETRACT", "code_status", "full code")],
}


def _extract(text, active):
    out = []
    for o in SCRIPT.get(text, []):
        o = dict(o)
        target = next((a for a in active if a.subject == o["subject"]), None)
        if target is not None and o["action"] in ("SUPERSEDE", "RETRACT"):
            o["target_id"] = target.id
        out.append(o)
    return out


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(llm_memory, "llm_extract_classify", _extract)
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)   # answer = facts used
    monkeypatch.setattr("vayl.memory.llm_client._embed", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])


@pytest.fixture
def uid():
    return f"u_{uuid.uuid4().hex[:8]}"   # a fresh memory space per test (the store is module-level)


def _active(uid, **space):
    return [x.value for x in s._store.load(uid, **space).active()]


def test_remember_supersedes_and_recall_returns_only_the_current_value(uid):
    assert "[ADD] state = Redux" in s.remember("We use Redux for state.", user_id=uid)
    assert "[SUPERSEDE] state = Zustand" in s.remember("We moved off Redux to Zustand.", user_id=uid)

    answer = s.recall("what do we use for state?", user_id=uid)
    assert "state=Zustand" in answer and "state=Redux" not in answer   # Redux is never a current fact
    assert _active(uid) == ["Zustand"]                               # persisted, not just returned


def test_a_statement_with_no_durable_fact_stores_nothing(uid):
    assert s.remember("hmm, interesting", user_id=uid).startswith("No durable fact")
    assert _active(uid) == []


def test_history_lists_every_value_oldest_first(uid):
    s.remember("We use Redux for state.", user_id=uid)
    s.remember("We moved off Redux to Zustand.", user_id=uid)
    out = s.history("state", user_id=uid)
    assert out.index("Redux  [SUPERSEDED]") < out.index("Zustand  [ACTIVE]")


def test_forget_retracts_but_keeps_the_fact_in_history(uid):
    s.remember("We use Sentry for monitoring.", user_id=uid)
    assert "Retracted" in s.forget("We dropped Sentry.", user_id=uid)
    assert "Sentry" not in s.recall("what do we use for monitoring?", user_id=uid)
    assert "Sentry" in s.history("monitoring", user_id=uid)
    assert s.forget("We dropped Sentry.", user_id=uid).startswith("Nothing matching")


def test_list_get_and_update_memory_by_id(uid):
    s.remember("We use Redux for state.", user_id=uid)
    mid = s._store.load(uid).active()[0].id
    assert f"#{mid}" in s.list_memories(user_id=uid)
    assert "state = Redux" in s.get_memory(mid, user_id=uid)

    assert "'Redux' → 'Jotai'" in s.update_memory(mid, "Jotai", user_id=uid)
    assert _active(uid) == ["Jotai"]
    assert "Redux" in s.history("state", user_id=uid)                 # audit-preserving
    assert s.get_memory(999_999, user_id=uid).startswith("No memory")
    assert s.update_memory(999_999, "x", user_id=uid).startswith("No memory")


def test_agent_and_run_ids_are_separate_memory_spaces(uid):
    s.remember("We use Redux for state.", user_id=uid, agent_id="frontend")
    s.remember("We use Sentry for monitoring.", user_id=uid, agent_id="ops")
    assert _active(uid, agent_id="frontend") == ["Redux"]
    assert _active(uid, agent_id="ops") == ["Sentry"]
    assert "Sentry" not in s.recall("what do we use?", user_id=uid, agent_id="frontend")


def test_record_decision_then_explain_it_with_a_verified_receipt(uid):
    s.remember("We use Redux for state.", user_id=uid)
    out = s.record_decision("Scaffolded the store module", "what do we use for state?", user_id=uid)
    did = int(out.split("#", 1)[1].split()[0])

    s.remember("We moved off Redux to Zustand.", user_id=uid)          # the belief changes later…
    explained = s.explain_decision(did, user_id=uid)
    assert "Scaffolded the store module" in explained
    assert "state = Redux" in explained                                # …but the decision kept it
    assert "receipt verified" in explained


def test_check_before_act_is_safe_or_blocked_by_policy(uid):
    s.remember("We use Redux for state.", user_id=uid)
    assert s.check_before_act("state", user_id=uid).startswith("✅ SAFE")
    blocked = s.check_before_act("state", user_id=uid, min_confidence=0.99)
    assert blocked.startswith("⛔ BLOCKED") and "confidence" in blocked


def test_attestation_is_a_verifiable_signed_receipt(uid):
    s.remember("We use Redux for state.", user_id=uid)
    out = s.attest("state", user_id=uid)
    rid = int(out.split("#", 1)[1].split(":")[0])
    assert "'state' = Redux" in out
    assert "✓ VALID" in s.verify_receipt(rid)


def test_delete_all_erases_and_issues_a_verifiable_erasure_receipt(uid):
    s.remember("We use Redux for state.", user_id=uid)
    out = s.delete_all(user_id=uid)
    rid = int(out.split("receipt #", 1)[1].split()[0])
    assert _active(uid) == []
    verdict = s.verify_receipt(rid)
    assert "erasure_receipt" in verdict and "✓ VALID" in verdict


def test_reconcile_policy_roundtrip_and_rejects_unknown_modes(uid):
    assert "RECENCY" in s.get_reconcile_policy(user_id=uid)
    assert "AUTHORITY" in s.set_reconcile_policy("AUTHORITY", {"crm": 2, "agent": 1}, user_id=uid)
    assert "AUTHORITY" in s.get_reconcile_policy(user_id=uid)
    assert s.set_reconcile_policy("LOUDEST", user_id=uid).startswith("Unknown mode")


def test_export_memory_is_complete_machine_readable_json(uid):
    s.remember("We use Redux for state.", user_id=uid)
    s.remember("We moved off Redux to Zustand.", user_id=uid)
    data = json.loads(s.export_memory(user_id=uid))
    by_value = {r["value"]: r["status"] for r in data["records"]}
    assert by_value == {"Redux": "SUPERSEDED", "Zustand": "ACTIVE"}    # active AND history
    assert data["count"] == 2
    assert {"decisions", "audit", "receipts"} <= data.keys()


def test_audit_chain_verifies_and_the_public_key_is_exported(uid):
    s.remember("We use Redux for state.", user_id=uid)
    assert "INTACT" in s.verify_audit()
    assert "Ed25519 public key" in s.export_public_key()


def test_health_reports_each_dependency():
    report = s.health()
    for part in ("db: ok", "embedder: ok", "llm: ok", "graph: disabled"):
        assert part in report


def _provider_400(*_a, **_k):
    import io
    import urllib.error
    body = b'{"error": {"message": "Unsupported value: temperature 0 is not supported with this model."}}'
    raise urllib.error.HTTPError("https://api.openai.com/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body))


def test_a_rejected_model_call_explains_itself(uid, monkeypatch):
    """A live gpt-6 run failed every call with only 'llm: FAIL (HTTPError)' and an opaque ref. health
    sends a fixed probe, so it can show the provider's reason; tools only name the HTTP status."""
    monkeypatch.setattr(llm_memory, "llm_extract_classify", _provider_400)
    assert "llm: FAIL (HTTP 400: Unsupported value: temperature 0" in s.health()
    out = s.remember("We use Redux for state.", user_id=uid)
    assert "rejected the request (HTTP 400)" in out and "health" in out
    assert "temperature" not in out          # provider text can echo the request, so tools never show it


def test_skip_is_not_reported_as_stored(uid):
    out = s.remember("If we used Vue it would be faster.", user_id=uid)
    assert "Stored" not in out and out.startswith("Not stored") and "framework = Vue" in out
    assert _active(uid) == []


def test_forget_on_a_gated_slot_reports_the_pending_proposal(uid, monkeypatch):
    """code_status needs approval to change, so forget PROPOSES the removal. The tool used to reply
    'Nothing matching to retract' while a proposal sat in the queue."""
    from vayl.memory.schema import load as load_schema
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", load_schema("preset:clinical"))
    s.remember("Patient is full code.", user_id=uid)
    out = s.forget("Remove the code status.", user_id=uid)
    assert out.startswith("Proposed for removal, awaiting approval") and "code_status" in out
    assert _active(uid) == ["full code"]                              # nothing applied yet
    pending = s.pending_changes(user_id=uid)
    assert "1 change(s) awaiting approval" in pending
    assert "REMOVE code_status: 'full code'" in pending and "->" not in pending   # no fake new value
    assert s.forget("Remove the code status.", user_id=uid).startswith("Already awaiting approval")


def test_deleting_nothing_issues_no_receipt(uid):
    before = s._store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    assert s.delete("no_such_subject", user_id=uid) == "No records found for 'no_such_subject'."
    assert s._store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == before


def test_server_reports_vayls_version():
    import vayl
    assert s.mcp.version == vayl.__version__


def test_the_server_tells_clients_when_to_use_memory():
    """Without server instructions, Claude Code asked to 'remember X for this project' wrote a notes
    file and never called Vayl. The instructions name the tools and the situations."""
    text = s.mcp.instructions or ""
    for word in ("`remember`", "`recall`", "`forget`", "include_history"):
        assert word in text


def test_the_review_tools_warn_before_two_anticoagulants_become_current(uid, monkeypatch):
    from vayl.memory.schema import load as load_schema
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", load_schema("preset:clinical"))
    shapes = {"On warfarin 5 mg daily.": [{"action": "ADD", "subject": "active_medication",
                                           "value": "warfarin 5 mg daily", "confidence": 0.95}],
              "Now on Eliquis 5 mg BID.": [{"action": "ADD", "subject": "active_medication",
                                            "value": "Eliquis 5 mg BID", "confidence": 0.95}]}
    monkeypatch.setattr(llm_memory, "llm_extract_classify", lambda text, active: shapes[text])
    s.remember("On warfarin 5 mg daily.", user_id=uid)
    s.remember("Now on Eliquis 5 mg BID.", user_id=uid)
    queue = s.pending_changes(user_id=uid)
    assert "ADD active_medication: 'Eliquis 5 mg BID'" in queue and "two anticoagulants" in queue
    pid = int(queue.split("#")[1].split()[0])
    approved = s.confirm_change(memory_id=pid, user_id=uid)
    assert approved.startswith(f"Approved #{pid}: ADD") and "'warfarin 5 mg daily' is still active" in approved
