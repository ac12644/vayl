"""Declared constraints over several facts, judged per message, with linked proposals that are approved
or rejected whole and re-checked at approval. Offline: extraction is scripted."""
import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import ConstraintViolation, LLMMemory, Status
from vayl.memory.reconcile import Action
from vayl.memory.schema import from_dict
from vayl.memory.schema import load as load_schema

MED = "active_medication"


@pytest.fixture(autouse=True)
def _auto(monkeypatch):
    schema = load_schema("preset:clinical")
    schema.resolve("active_medication").confirm_mode = "auto"
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", schema)
    monkeypatch.setattr(llm_memory, "_TRUSTED_SOURCES", ("fhir",))
    monkeypatch.setattr(llm_memory, "_CONFIRM_MODE", "")


def _say(m, monkeypatch, text, *facts, source=""):
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    monkeypatch.setattr(llm_memory, "llm_extract_classify", lambda t, c: [{**base, **f} for f in facts])
    return m.add(text, source=source)


def _chart(monkeypatch, *meds):
    m = LLMMemory()
    for v in meds or ("warfarin 5 mg daily", "metformin 500 mg twice daily"):
        _say(m, monkeypatch, v, {"action": "ADD", "subject": MED, "value": v}, source="fhir")
    return m


def _meds(m):
    return sorted(s.value for s in m.active() if s.subject == MED)


def _groups(m):
    return {(s.metadata or {}).get("group") for s in m.pending()}


# ── the schema ──

def test_constraints_load_and_bad_ones_are_rejected():
    c = load_schema("preset:clinical").constraints[0]
    assert (c.name, c.slot, c.at_most) == ("one_anticoagulant", MED, 1) and "apixaban" in c.members
    with pytest.raises(ValueError, match="not a declared slot"):
        from_dict({"slots": [{"name": "x"}], "constraints": [{"slot": "y"}]})
    with pytest.raises(ValueError, match="at_most"):
        from_dict({"slots": [{"name": "x"}], "constraints": [{"slot": "x", "at_most": 0}]})


# ── judged per message, held whole ──

def test_a_switch_missing_its_stop_is_held_as_one_linked_proposal(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Switched from warfarin to apixaban 5 mg BID.",
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"})
    assert _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    assert len(m.pending()) == 2 and len(_groups(m)) == 1 and None not in _groups(m)
    add = next(p for p in m.pending() if p.value == "apixaban 5 mg BID")
    assert m.confirm(add.id)[0] is Action.ADD                           # approving one applies both
    assert _meds(m) == ["apixaban 5 mg BID", "metformin 500 mg twice daily"] and not m.pending()


def test_rejecting_one_linked_proposal_rejects_the_group(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Switched from warfarin to apixaban 5 mg BID.",
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"})
    m.reject(m.pending()[0].id)
    assert not m.pending() and _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]


def test_changes_outside_the_broken_rule_still_apply(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Start lisinopril 10 mg. Switched from warfarin to apixaban 5 mg BID.",
         {"action": "ADD", "subject": MED, "value": "lisinopril 10 mg daily"},
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"})
    assert "lisinopril 10 mg daily" in _meds(m)                         # not an anticoagulant: stands
    assert sorted(p.value for p in m.pending()) == ["apixaban 5 mg BID", "warfarin 5 mg daily"]


def test_a_message_that_breaks_the_rule_has_all_its_in_scope_changes_taken_back(monkeypatch):
    """Every change was clear on its own and auto-applied; together they leave two anticoagulants, so
    all three come off the record, the warfarin stop included."""
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Stop warfarin. Start apixaban 5 mg BID and rivaroxaban 20 mg daily.",
         {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1},
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"},
         {"action": "ADD", "subject": MED, "value": "rivaroxaban 20 mg daily"})
    assert _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    assert sorted((p.metadata["pending"], p.value) for p in m.pending()) == [
        ("ADD", "apixaban 5 mg BID"), ("ADD", "rivaroxaban 20 mg daily"), ("RETRACT", "warfarin 5 mg daily")]
    assert len(_groups(m)) == 1
    assert not [s for s in m.statements if (s.metadata or {}).get("auto_applied")]
    with pytest.raises(ConstraintViolation):                           # still two DOACs if approved
        m.confirm(m.pending()[0].id)


def test_a_clean_switch_still_applies_by_itself(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "D/C warfarin. Start apixaban 5mg bid.",
         {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1},
         {"action": "ADD", "subject": MED, "value": "apixaban 5mg bid"})
    assert _meds(m) == ["apixaban 5mg bid", "metformin 500 mg twice daily"] and not m.pending()


def test_an_approved_overlap_does_not_block_later_changes(monkeypatch):
    """Bridging: enoxaparin beside warfarin was decided by a person. A warfarin dose change doesn't make
    it worse, so it applies."""
    m = _chart(monkeypatch, "warfarin 5 mg daily", "enoxaparin 80 mg twice daily")
    _say(m, monkeypatch, "Increase warfarin to 7.5 mg daily.",
         {"action": "SUPERSEDE", "subject": MED, "value": "warfarin 7.5 mg daily", "target_id": 1})
    assert _meds(m) == ["enoxaparin 80 mg twice daily", "warfarin 7.5 mg daily"]


# ── re-checked at approval ──

def test_approval_is_rechecked_against_the_record_as_it_is_then(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Switched from warfarin to apixaban 5 mg BID.",
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"})
    pid = m.pending()[0].id
    assert m.violations(pid) == []                                      # fine when proposed
    _say(m, monkeypatch, "rivaroxaban 20 mg daily", {"action": "ADD", "subject": MED,
                                                    "value": "rivaroxaban 20 mg daily"}, source="fhir")
    with pytest.raises(ConstraintViolation, match="one_anticoagulant"):
        m.confirm(pid)                                                  # the feed added a DOAC meanwhile
    assert len(m.pending()) == 2                                        # refused whole, nothing applied
    m.confirm(pid, source="dr_okafor", override=True)
    assert {s.value for s in m.statements if (s.metadata or {}).get("override")} == {
        "apixaban 5 mg BID", "(retracted: warfarin 5 mg daily)"}


def test_a_group_with_one_stale_member_goes_stale_whole(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Switched from warfarin to apixaban 5 mg BID.",
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"})
    next(s for s in m.statements if s.id == 1).status = Status.SUPERSEDED   # warfarin removed elsewhere
    assert m.confirm(m.pending()[0].id) is None and not m.pending()
    assert "apixaban 5 mg BID" not in _meds(m)


# ── not clinical: any slot, any cardinality ──

def test_a_cardinality_rule_on_another_domain(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", from_dict({
        "slots": [{"name": "subscription", "multi": True, "confirm": "auto"}],
        "constraints": [{"name": "one_plan", "slot": "subscription", "at_most": 1}]}))
    m = LLMMemory()
    _say(m, monkeypatch, "On the Team plan.", {"action": "ADD", "subject": "subscription", "value": "Team plan"},
         source="fhir")
    _say(m, monkeypatch, "Also on Enterprise.", {"action": "ADD", "subject": "subscription", "value": "Enterprise plan"})
    assert [s.value for s in m.active()] == ["Team plan"]
    assert [p.value for p in m.pending()] == ["Enterprise plan"]
