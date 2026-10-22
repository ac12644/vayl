"""confirm "auto" on the built-in clinical preset: a clear medication change applies at once (and is
marked auto-applied); only an uncertain one waits for a person. Offline: extraction is scripted with
the shapes real models returned in benchmarks/evaluations/med_change_safety.py."""
import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import LLMMemory
from vayl.memory.schema import load as load_schema


@pytest.fixture(autouse=True)
def _auto(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", load_schema("preset:clinical"))   # meds: "auto"
    monkeypatch.setattr(llm_memory, "_TRUSTED_SOURCES", ("fhir",))
    monkeypatch.setattr(llm_memory, "_CONFIRM_MODE", "")


def _chart():
    m = LLMMemory()
    for subj, val in [("active_medication", "warfarin 5 mg daily"),
                      ("active_medication", "metformin 500 mg twice daily"), ("code_status", "Full code")]:
        m._apply({"action": "ADD", "subject": subj, "value": val, "confidence": 0.95, "kind": "state",
                  "time_ref": "present"}, val, source="fhir")
    return m


def _say(m, monkeypatch, text, *facts):
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    monkeypatch.setattr(llm_memory, "llm_extract_classify", lambda t, c: [{**base, **f} for f in facts])
    return m.add(text)


def _meds(m):
    return sorted(s.value for s in m.active() if s.subject == "active_medication")


def _queue(m):
    return sorted(((p.metadata or {}).get("pending"), p.value) for p in m.pending())


MED = "active_medication"


def test_a_clear_switch_applies_whole_and_is_marked(monkeypatch):
    m = _chart()
    _say(m, monkeypatch, "D/C warfarin. Start apixaban 5mg bid.",
         {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1},
         {"action": "ADD", "subject": MED, "value": "apixaban 5mg bid"})
    assert _meds(m) == ["apixaban 5mg bid", "metformin 500 mg twice daily"] and not m.pending()
    marked = [s for s in m.statements if (s.metadata or {}).get("auto_applied")]
    assert {s.value for s in marked} == {"apixaban 5mg bid", "(retracted: warfarin 5 mg daily)"}


def test_a_switch_missing_its_stop_is_held_whole_never_half(monkeypatch):
    """The model returned only ADD apixaban: applying it would leave two anticoagulants."""
    m = _chart()
    _say(m, monkeypatch, "Switched from warfarin to apixaban 5 mg BID.",
         {"action": "ADD", "subject": MED, "value": "apixaban 5 mg BID"})
    assert _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    assert _queue(m) == [("ADD", "apixaban 5 mg BID"), ("RETRACT", "warfarin 5 mg daily")]


def test_a_new_drug_a_stop_and_a_dose_change_apply_at_once(monkeypatch):
    m = _chart()
    _say(m, monkeypatch, "Started lisinopril 10 mg daily.",
         {"action": "ADD", "subject": MED, "value": "lisinopril 10 mg daily"})
    _say(m, monkeypatch, "Increase metformin to 1000 mg twice daily.",
         {"action": "SUPERSEDE", "subject": MED, "value": "metformin 1000 mg twice daily", "target_id": 2})
    _say(m, monkeypatch, "Warfarin stopped today.",
         {"action": "ARCHIVE", "subject": MED, "value": "warfarin stopped today", "time_ref": "past"})
    assert _meds(m) == ["lisinopril 10 mg daily", "metformin 1000 mg twice daily"] and not m.pending()


@pytest.mark.parametrize("text, fact", [
    ("We should probably stop the warfarin if her INR stays high.",            # hedge
     {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1}),
    ("Do not stop the warfarin.",                                               # negation
     {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1}),
    ("Don't switch her to apixaban yet.",
     {"action": "SUPERSEDE", "subject": MED, "value": "apixaban 5 mg BID", "target_id": 1}),
    ("Stop warfarin.",                                                          # low confidence
     {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1, "confidence": 0.5}),
    ("Start apixaban tomorrow.",                                                # future-tense order
     {"action": "SUPERSEDE", "subject": MED, "value": "apixaban 5 mg BID", "target_id": 1, "time_ref": "future"}),
    ("Switch from warfarin.",                                                   # guessed action
     {"action": "RETIRE", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1}),
    ("Metformin held for contrast.",                                            # rerouted subject
     {"action": "SUPERSEDE", "subject": "metformin_therapy_status", "value": "metformin on hold", "target_id": 2}),
])
def test_an_uncertain_change_never_applies_by_itself(monkeypatch, text, fact):
    m = _chart()
    _say(m, monkeypatch, text, fact)
    assert _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]


def test_code_status_stays_on_review_in_the_preset(monkeypatch):
    m = _chart()
    _say(m, monkeypatch, "Patient is now DNR.", {"action": "SUPERSEDE", "subject": "code_status", "value": "DNR",
                                                  "target_id": 3})
    assert next(s.value for s in m.active() if s.subject == "code_status") == "Full code"
    assert _queue(m) == [("SUPERSEDE", "DNR")]


def test_vayl_confirm_mode_review_overrides_the_preset(monkeypatch):
    monkeypatch.setattr(llm_memory, "_CONFIRM_MODE", "review")
    m = _chart()
    _say(m, monkeypatch, "D/C warfarin. Start apixaban 5mg bid.",
         {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1},
         {"action": "ADD", "subject": MED, "value": "apixaban 5mg bid"})
    assert _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    assert _queue(m) == [("ADD", "apixaban 5mg bid"), ("RETRACT", "warfarin 5 mg daily")]


def test_a_supersede_naming_no_current_drug_is_a_new_item_not_an_ungated_write(monkeypatch):
    monkeypatch.setattr(llm_memory, "_CONFIRM_MODE", "review")
    m = _chart()
    _say(m, monkeypatch, "Now on atorvastatin 40 mg.",
         {"action": "SUPERSEDE", "subject": MED, "value": "atorvastatin 40 mg nightly"})
    assert "atorvastatin 40 mg nightly" not in _meds(m)
    assert _queue(m) == [("ADD", "atorvastatin 40 mg nightly")]


def test_an_invalid_confirm_mode_is_rejected():
    from vayl.memory.schema import from_dict
    with pytest.raises(ValueError, match="confirm must be"):
        from_dict({"slots": [{"name": "x", "confirm": "sometimes"}]})


# ── found by replaying recorded model outputs (benchmarks/evaluations/med_change_replay.py) ──

@pytest.mark.parametrize("text, facts", [
    ("Hold metformin tonight before the CT scan.",
     [{"action": "ADD", "subject": MED, "value": "metformin 500 mg twice daily — hold tonight before CT scan"}]),
    ("Hold metformin tonight before the CT scan.", [{"action": "ADD", "subject": MED, "value": "hold metformin tonight before the CT scan"}]),
    ("Metformin held for contrast, resume in 48h.", [{"action": "ADD", "subject": MED, "value": "Metformin held for contrast"}]),
    ("Restart metformin 500 mg twice daily.",
     [{"action": "SUPERSEDE", "subject": MED, "value": "metformin 500 mg twice daily (restarted)", "target_id": 2}]),
    ("Continue warfarin, stop metformin.", [{"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily", "target_id": 1},
                                           {"action": "RETRACT", "subject": MED, "value": "metformin 500 mg twice daily", "target_id": 2}]),
    ("Continue warfarin, stop metformin.", [{"action": "RETRACT", "subject": MED, "value": "metformin 500 mg twice daily", "target_id": 2},
                                           {"action": "ADD", "subject": MED, "value": "warfarin (continue)"}]),
    ("Don't switch her to apixaban yet.", [{"action": "SUPERSEDE", "subject": MED, "value": "apixaban 5 mg BID", "target_id": 1}]),
])
def test_holds_keeps_and_negations_never_change_or_queue_the_wrong_drug(monkeypatch, text, facts):
    m = _chart()
    _say(m, monkeypatch, text, *facts)
    meds = _meds(m)
    assert "warfarin 5 mg daily" in meds                               # never removed or rewritten
    assert all("hold" not in v.lower() and "held" not in v.lower() and "(" not in v for v in meds)
    assert not [p for p in m.pending() if "warfarin" in p.value.lower() or p.supersedes == 1]
    if "stop metformin" in text.lower():
        assert "metformin 500 mg twice daily" not in meds              # the real stop still applies


def test_a_stop_naming_two_drugs_queues_both_instead_of_storing_the_sentence(monkeypatch):
    m = _chart()
    _say(m, monkeypatch, "Stop warfarin and metformin.", {"action": "ADD", "subject": MED, "value": "Stop warfarin and metformin."})
    assert _meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    assert sorted(p.supersedes for p in m.pending()) == [1, 2]


def test_a_dose_change_keeps_the_drug_name_findable(monkeypatch):
    m = _chart()
    _say(m, monkeypatch, "Increase metformin to 1000 mg twice daily.",
         {"action": "SUPERSEDE", "subject": MED, "value": "metformin to 1000 mg twice daily", "target_id": 2})
    _say(m, monkeypatch, "Stop metformin.", {"action": "RETRACT", "subject": MED, "value": "metformin"})
    assert _meds(m) == ["warfarin 5 mg daily"]                         # the later stop found it
