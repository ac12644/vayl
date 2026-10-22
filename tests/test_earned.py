"""Earned autonomy: which later writes count as corrections of an auto-applied write, and a slot that is
"auto" only while the current extractor has earned it. Offline: extraction is scripted."""
import time

import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import LLMMemory
from vayl.memory.schema import from_dict

MED = "active_medication"


def _schema(mode):
    return from_dict({"slots": [{"name": MED, "multi": True, "confirm": mode}]})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", _schema("auto"))
    monkeypatch.setattr(llm_memory, "_TRUSTED_SOURCES", ("fhir",))
    monkeypatch.setattr(llm_memory, "_CONFIRM_MODE", "")
    monkeypatch.setenv("VAYL_EXTRACTOR_ID", "openai:gpt-6-luna")


def _say(m, monkeypatch, text, *facts, source=""):
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    monkeypatch.setattr(llm_memory, "llm_extract_classify", lambda t, c: [{**base, **f} for f in facts])
    return m.add(text, source=source)


def _dose(m, monkeypatch, value, text=None, tid=None):
    tid = tid or next(s.id for s in m.active() if s.subject == MED and "warfarin" in s.value)
    _say(m, monkeypatch, text or f"Warfarin now {value}.", {"action": "SUPERSEDE", "subject": MED, "value": value,
                                                            "target_id": tid})


def _chart(monkeypatch):
    m = LLMMemory()
    _say(m, monkeypatch, "warfarin", {"action": "ADD", "subject": MED, "value": "warfarin 5 mg daily"}, source="fhir")
    return m


def _auto(m):
    return next(s for s in m.statements if (s.metadata or {}).get("auto_applied"))


# ── the discriminator ──

def test_going_back_to_the_exact_prior_value_is_a_reversal(monkeypatch):
    m = _chart(monkeypatch)
    _dose(m, monkeypatch, "warfarin 7.5 mg daily")
    _dose(m, monkeypatch, "warfarin 5 mg daily", "Warfarin 5 mg daily.")
    assert _auto(m).metadata["corrected"] == "reversal"


def test_a_later_different_value_is_a_legitimate_change(monkeypatch):
    m = _chart(monkeypatch)
    _dose(m, monkeypatch, "warfarin 7.5 mg daily")
    _dose(m, monkeypatch, "warfarin 10 mg daily")
    a = _auto(m)
    assert "corrected" not in a.metadata and a.metadata["changed_by"]


def test_correction_language_aimed_at_it_is_a_correction(monkeypatch):
    m = _chart(monkeypatch)
    _dose(m, monkeypatch, "warfarin 7.5 mg daily")
    _dose(m, monkeypatch, "warfarin 6 mg daily", "Correction: warfarin is 6 mg, the 7.5 was entered wrong.")
    assert _auto(m).metadata["corrected"] == "language"


def test_an_edit_of_it_is_a_correction(monkeypatch):
    m = _chart(monkeypatch)
    _dose(m, monkeypatch, "warfarin 7.5 mg daily")
    m.update(_auto(m).id, "warfarin 6 mg daily", source="dr_okafor")
    assert _auto(m).metadata["corrected"] == "edit"


def test_an_auto_added_item_removed_again_and_an_auto_stop_undone_are_reversals(monkeypatch):
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Start lisinopril 10 mg daily.", {"action": "ADD", "subject": MED, "value": "lisinopril 10 mg daily"})
    added = next(s for s in m.active() if "lisinopril" in s.value)
    _say(m, monkeypatch, "Stop lisinopril.", {"action": "RETRACT", "subject": MED, "value": "lisinopril 10 mg daily",
                                              "target_id": added.id})
    assert added.metadata["corrected"] == "reversal"
    _say(m, monkeypatch, "Stop warfarin.", {"action": "RETRACT", "subject": MED, "value": "warfarin 5 mg daily",
                                            "target_id": 1})
    stop = next(s for s in m.statements if (s.metadata or {}).get("auto_applied") and "retracted: warfarin" in s.value)
    _say(m, monkeypatch, "Warfarin 5 mg daily.", {"action": "ADD", "subject": MED, "value": "warfarin 5 mg daily"},
         source="fhir")
    assert stop.metadata["corrected"] == "reversal"


def test_a_reversal_long_after_is_not_counted(monkeypatch):
    m = _chart(monkeypatch)
    _dose(m, monkeypatch, "warfarin 7.5 mg daily")
    _auto(m).created_at = time.time() - 30 * 86400
    _dose(m, monkeypatch, "warfarin 5 mg daily", "Warfarin 5 mg daily.")
    assert "corrected" not in _auto(m).metadata


# ── the earned mode ──

def _clear_change(m, monkeypatch, i):
    _say(m, monkeypatch, f"Start drug{i} 10 mg daily.", {"action": "ADD", "subject": MED, "value": f"drug{i} 10 mg daily"})


def test_a_new_extractor_starts_in_shadow_and_earns_auto(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", _schema("earned"))
    monkeypatch.setattr(llm_memory, "_EARN_MIN", 3)
    m = _chart(monkeypatch)
    for i in range(3):
        _clear_change(m, monkeypatch, i)
        (p,) = m.pending()
        assert p.metadata["would_auto"] and p.metadata["extractor"] == "openai:gpt-6-luna"
        m.confirm(p.id)                                   # a person agreed with what it would have done
    assert m.autonomy(MED) == (3, 0)
    _clear_change(m, monkeypatch, 3)
    assert not m.pending() and _auto(m).value == "drug3 10 mg daily"     # earned: applied by itself


def test_an_uncertain_change_is_not_shadow_evidence(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", _schema("earned"))
    m = _chart(monkeypatch)
    _say(m, monkeypatch, "Maybe start drug9.", {"action": "ADD", "subject": MED, "value": "drug9 10 mg daily"})
    assert "would_auto" not in m.pending()[0].metadata


def test_corrections_take_autonomy_away_and_a_new_model_starts_over(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", _schema("earned"))
    monkeypatch.setattr(llm_memory, "_EARN_MIN", 3)
    monkeypatch.setattr(llm_memory, "_EARN_MAX_RATE", 0.25)
    m = _chart(monkeypatch)
    for i in range(3):
        _clear_change(m, monkeypatch, i)
        m.confirm(m.pending()[0].id)
    _dose(m, monkeypatch, "warfarin 7.5 mg daily")        # auto now
    _dose(m, monkeypatch, "warfarin 5 mg daily", "Warfarin 5 mg daily.")   # ...and reversed: an error
    assert m.autonomy(MED) == (5, 1)                     # 3 shadow + 2 auto, 1 corrected: 20% still holds
    _dose(m, monkeypatch, "warfarin 2.5 mg daily")
    _dose(m, monkeypatch, "warfarin 5 mg daily", "No, she is still on 5 mg.")
    assert m.autonomy(MED) == (7, 2) and m._mode(llm_memory.SLOT_SCHEMA.resolve(MED)) == "shadow"
    monkeypatch.setenv("VAYL_EXTRACTOR_ID", "openai:gpt-6.1-luna")
    assert m.autonomy(MED) == (0, 0)                     # a new model has earned nothing
