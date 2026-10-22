"""
The clinical slot preset on the reconciliation engine: list slots that hold several values, critical
tagging, confirmation gates, the trusted-source bypass, and the longitudinal patient acceptance suite.

Facts are written in the shape an authorized structured feed delivers them (source="fhir"), so these
pin the ENGINE's behavior for clinical data, independent of any ingestion adapter.
"""

import os

import pytest

os.environ.setdefault("VAYL_SLOT_SCHEMA",
                      os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   "examples", "clinical-slots.json"))
os.environ.setdefault("VAYL_CRITICAL_CATEGORIES", "critical")
os.environ.setdefault("VAYL_SLOT_RESOLVE", "1")
from benchmarks.clinical.patients import PATIENTS  # noqa: E402
from benchmarks.clinical.run import check, ingest  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402
from vayl.memory.llm_memory import LLMMemory, Status, is_critical  # noqa: E402
from vayl.memory.reconcile import Action  # noqa: E402
from vayl.memory.schema import load as _load_schema  # noqa: E402

_CLINICAL = _load_schema(os.environ["VAYL_SLOT_SCHEMA"])


@pytest.fixture(autouse=True)
def _clinical_env(monkeypatch):
    """One clinical configuration for every test in this file: the schema loads once at import, so
    pin it regardless of collection order, enable list-slot resolution, and trust the authorized
    order feeds (fhir + inpatient) while narrative stays gated."""
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", _CLINICAL)
    monkeypatch.setattr(llm_memory, "_CRITICAL_CATEGORIES", ("critical",))
    monkeypatch.setattr(llm_memory, "_SLOT_RESOLVE", True)
    monkeypatch.setattr(llm_memory, "_TRUSTED_SOURCES", ("inpatient", "fhir"))


def _feed(action, subject, value):
    """A fact as an authorized structured feed (e.g. FHIR) delivers it."""
    return {"subject": subject, "value": value, "action": action, "kind": "state", "scope": "global",
            "time_ref": "present", "confidence": 0.98, "source": "fhir"}


def _apply_all(m, facts):
    for f in facts:
        m._apply(f, f.get("value", ""), source=f.get("source", "fhir"))


def test_a_structured_feed_produces_correct_current_truth():
    m = LLMMemory()
    _apply_all(m, [
        _feed("ADD", "allergy", "Penicillin — Anaphylaxis (severe)"),
        _feed("ADD", "allergy", "Sulfa — Rash (moderate)"),                 # list: BOTH coexist
        _feed("ADD", "active_medication", "Warfarin 5 mg PO daily"),
        _feed("ADD", "active_medication", "Metformin 1000 mg PO BID"),
        _feed("ADD", "primary_diagnosis", "Community-acquired pneumonia"),
    ])
    allergies = {s.value.split(" —")[0] for s in m.active() if s.subject == "allergy"}
    assert allergies == {"Penicillin", "Sulfa"}             # neither deleted the other
    assert all(is_critical(s) for s in m.active() if s.subject == "allergy")
    meds = {s.value for s in m.active() if s.subject == "active_medication"}
    assert meds == {"Warfarin 5 mg PO daily", "Metformin 1000 mg PO BID"}


def test_a_refuted_allergy_leaves_the_active_chart_but_stays_in_history():
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "allergy", "Penicillin — Rash (severe)")])
    assert any(s.subject == "allergy" for s in m.active())
    _apply_all(m, [_feed("RETRACT", "allergy", "Penicillin — Rash (severe)")])
    assert not any("Penicillin" in s.value for s in m.active() if s.subject == "allergy")
    assert any(s.subject == "allergy" and s.status is not Status.ACTIVE for s in m.statements)


def test_a_narrative_change_is_still_gated_while_the_feed_is_trusted():
    """The bypass is source-scoped: a trusted feed's order applies directly, but a change from the
    narrative path (source not trusted) still queues for review. Both on the same confirm slot."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "Warfarin 5 mg PO daily")])   # trusted -> active
    m._apply({"action": "RETRACT", "subject": "active_medication", "value": "Warfarin 5 mg PO daily",
              "scope": "global", "confidence": 0.9, "time_ref": "present"},
             "note says consider stopping warfarin", source="note_extractor")
    assert any("Warfarin" in s.value for s in m.active())              # still current
    assert len(m.pending()) == 1                                       # queued for a human


def test_a_stopped_medication_leaves_the_list_but_others_remain():
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "Warfarin 5 mg PO daily"),
                   _feed("ADD", "active_medication", "Metformin 1000 mg PO BID"),
                   _feed("ADD", "active_medication", "Atorvastatin 40 mg PO daily")])
    _apply_all(m, [_feed("RETRACT", "active_medication", "Warfarin 5 mg PO daily")])
    current = {s.value.split(" ")[0] for s in m.active() if s.subject == "active_medication"}
    assert "Warfarin" not in current
    assert {"Metformin", "Atorvastatin"} <= current         # the rest untouched


def test_a_refuted_allergy_matches_by_substance_not_exact_text():
    """A refute may carry different reaction detail than what was charted. The substance is the
    identity: 'Penicillin — Anaphylaxis (severe)' is retracted by a refute of 'Penicillin —
    Anaphylaxis', because both are the penicillin allergy."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "allergy", "Penicillin — Anaphylaxis (severe)")])
    _apply_all(m, [_feed("RETRACT", "allergy", "Penicillin — Anaphylaxis")])
    assert not any("Penicillin" in s.value for s in m.active() if s.subject == "allergy")


def test_identity_matching_does_not_hit_a_different_drug():
    """Stopping warfarin must not remove metformin — different identities, no cross-match."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "Warfarin 5 mg PO daily"),
                   _feed("ADD", "active_medication", "Metformin 1000 mg PO BID")])
    _apply_all(m, [_feed("RETRACT", "active_medication", "Warfarin 5 mg PO daily")])
    current = {s.value.split(" ")[0] for s in m.active() if s.subject == "active_medication"}
    assert current == {"Metformin"}


# ── longitudinal patient acceptance suite ──

@pytest.mark.parametrize("patient", PATIENTS, ids=lambda p: p["patient_id"])
def test_every_clinical_guarantee_holds(patient):
    m = ingest(patient)
    failures = [label for good, label in check(patient, m) if not good]
    assert not failures, f"{patient['patient_id']}: " + "; ".join(failures)


def test_a_second_allergy_does_not_delete_the_first():
    """The catastrophic bug realistic data found: single-valued allergy slots made a second allergy
    supersede the first, so penicillin + sulfa left only one on the chart."""
    from vayl.memory.llm_memory import LLMMemory, Status
    m = LLMMemory()
    for val in ("penicillin — anaphylaxis", "sulfa — rash", "latex — contact dermatitis"):
        m._apply({"action": "ADD", "subject": "allergy", "value": val, "scope": "global",
                  "confidence": 0.95, "time_ref": "present", "kind": "state"}, val)
    active = [s.value for s in m.active() if s.subject == "allergy"]
    assert len(active) == 3                       # all three coexist, none deleted
    assert all(s.status is Status.ACTIVE for s in m.active())


def test_a_repeated_allergy_still_dedups_on_a_list_slot():
    """Coexistence is for DISTINCT allergies; the same one re-entered is still one."""
    from vayl.memory.llm_memory import LLMMemory
    m = LLMMemory()
    for _ in range(3):
        m._apply({"action": "ADD", "subject": "allergy", "value": "penicillin", "scope": "global",
                  "confidence": 0.95, "time_ref": "present", "kind": "state"}, "penicillin allergy")
    assert len([s for s in m.active() if s.subject == "allergy"]) == 1


def test_stopping_one_list_medication_does_not_touch_the_others():
    """A retract on a list slot must target the named drug by value, not an arbitrary member."""
    from vayl.memory.llm_memory import LLMMemory
    m = LLMMemory()
    for drug in ("warfarin 5 mg daily", "metformin 500 mg BID", "atorvastatin 40 mg daily"):
        m._apply({"action": "ADD", "subject": "active_medication", "value": drug, "scope": "global",
                  "confidence": 0.95, "time_ref": "present", "kind": "state"}, drug, source="fhir")
    # stop warfarin — a confirm slot, so it proposes; the OTHER two must be untouched and current
    m._apply({"action": "RETRACT", "subject": "active_medication", "value": "warfarin 5 mg daily",
              "scope": "global", "confidence": 0.95, "time_ref": "present", "kind": "state"},
             "hold warfarin")
    current = {s.value for s in m.active()}
    assert "metformin 500 mg BID" in current and "atorvastatin 40 mg daily" in current
    assert "warfarin 5 mg daily" in current       # held, not removed until approved
    assert len(m.pending()) == 1


# ── free-text medication changes (the shapes a real extractor returns) ──
# Measured with gpt-6-luna before the fix (benchmarks/evaluations/med_change_safety.py): 34 of 50
# switches left warfarin AND apixaban active with nothing awaiting approval, because the model
# returned the stop as a RETRACT (gated) but the new drug as a plain ADD (applied at once).

def _chart():
    """warfarin + metformin on the list, seeded by the trusted feed, as a real chart would be."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "warfarin 5 mg daily"),
                   _feed("ADD", "active_medication", "metformin 500 mg twice daily"),
                   _feed("ADD", "code_status", "Full code")])
    return m


def _narrative(m, text, *facts):
    """Apply extractor output for one sentence, from the untrusted narrative path."""
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    return [m._apply({**base, **f}, text) for f in facts]


def _current_meds(m):
    return sorted(s.value for s in m.active() if s.subject == "active_medication")


def test_a_switch_returned_as_retract_plus_add_changes_nothing_until_approved():
    m = _chart()
    _narrative(m, "D/C warfarin. Start apixaban 5mg bid.",
               {"action": "RETRACT", "subject": "active_medication", "value": "warfarin 5 mg daily"},
               {"action": "ADD", "subject": "active_medication", "value": "apixaban 5mg bid"})
    assert _current_meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    kinds = sorted((p.metadata or {}).get("pending") for p in m.pending())
    assert kinds == ["ADD", "RETRACT"]                                  # both halves wait for a person


def test_a_past_tense_stop_is_proposed_not_archived():
    """'Warfarin stopped today' came back as a past-tense ADD; the valid-time gate archived it and
    warfarin stayed active with no proposal. 8 of 10 'discontinued yesterday' runs were lost this way."""
    m = _chart()
    _narrative(m, "Warfarin was discontinued yesterday.",
               {"action": "ADD", "subject": "active_medication", "value": "warfarin was discontinued yesterday",
                "time_ref": "past"})
    (p,) = m.pending()
    assert (p.metadata or {}).get("pending") == "RETRACT"
    assert next(s for s in m.statements if s.id == p.supersedes).value == "warfarin 5 mg daily"
    assert "warfarin 5 mg daily" in _current_meds(m)                  # still current until approved
    m2 = _chart()                                                      # the model's other shape:
    _narrative(m2, "Warfarin stopped today.",                         # it names the action ARCHIVE
               {"action": "ARCHIVE", "subject": "active_medication", "value": "warfarin stopped today"})
    assert [(p.metadata or {}).get("pending") for p in m2.pending()] == ["RETRACT"]
    m3 = _chart()                                                      # gpt-6-luna's shape: a
    _narrative(m3, "Warfarin was discontinued yesterday.",            # targeted removal tagged
               {"action": "RETRACT", "subject": "active_medication",  # as a past "event"
                "value": "Warfarin was discontinued yesterday.", "target_id": 1, "time_ref": "past",
                "kind": "event"})
    assert [(p.metadata or {}).get("pending") for p in m3.pending()] == ["RETRACT"]
    m4 = _chart()
    _narrative(m4, "Warfarin discontinued.", {"action": "RETRACT", "subject": "active_medication",
                                              "value": "warfarin", "target_id": 1, "kind": "event"})
    assert [(p.metadata or {}).get("pending") for p in m4.pending()] == ["RETRACT"]


def test_a_future_tense_order_on_a_gated_slot_reaches_the_queue():
    """gpt-6-sol read 'Increase metformin to 1000 mg' and 'Start apixaban' as future: they were
    flagged outside the approval queue, so pending_changes never showed them."""
    m = _chart()
    _narrative(m, "Increase metformin to 1000 mg twice daily.",
               {"action": "SUPERSEDE", "subject": "active_medication", "value": "metformin 1000 mg twice daily",
                "target_id": 2, "time_ref": "future"})
    _narrative(m, "Start apixaban 5mg bid.",
               {"action": "ADD", "subject": "active_medication", "value": "apixaban 5mg bid", "time_ref": "future"})
    assert sorted((p.metadata or {}).get("pending") for p in m.pending()) == ["ADD", "SUPERSEDE"]
    assert _current_meds(m) == ["metformin 500 mg twice daily", "warfarin 5 mg daily"]
    empty = LLMMemory()                                                # the first drug, future-tensed,
    _narrative(empty, "Start warfarin tomorrow.",                     # must not become current now
               {"action": "ADD", "subject": "active_medication", "value": "warfarin 5 mg daily", "time_ref": "future"})
    assert _current_meds(empty) == []


def test_a_stop_sentence_stored_as_a_drug_becomes_a_removal_proposal():
    m = _chart()
    _narrative(m, "Stop the warfarin.",
               {"action": "ADD", "subject": "active_medication", "value": "Stop the warfarin."})
    assert "Stop the warfarin." not in _current_meds(m)
    assert [(p.metadata or {}).get("pending") for p in m.pending()] == ["RETRACT"]


def test_a_hold_or_a_hedge_is_never_a_stop():
    m = _chart()
    _narrative(m, "We should probably stop the warfarin if her INR stays high.",
               {"action": "ADD", "subject": "active_medication", "value": "stop warfarin if INR stays high"})
    assert not [p for p in m.pending() if (p.metadata or {}).get("pending") == "RETRACT"]
    m2 = _chart()
    _narrative(m2, "Hold metformin tonight before the CT scan.",
               {"action": "ADD", "subject": "active_medication", "value": "hold metformin tonight"})
    assert "metformin 500 mg twice daily" in _current_meds(m2)
    assert not [p for p in m2.pending() if (p.metadata or {}).get("pending") == "RETRACT"]


def test_a_code_status_change_labelled_add_is_still_gated():
    """On a single-valued gated slot the same-slot invariant would otherwise supersede 'Full code'."""
    m = _chart()
    _narrative(m, "Patient is now DNR.", {"action": "ADD", "subject": "code_status", "value": "DNR"})
    assert next(s.value for s in m.active() if s.subject == "code_status") == "Full code"
    assert [(p.metadata or {}).get("pending") for p in m.pending()] == ["SUPERSEDE"]


def test_a_proposed_new_drug_is_added_only_on_confirm():
    m = _chart()
    _narrative(m, "Started lisinopril 10 mg daily.",
               {"action": "ADD", "subject": "active_medication", "value": "lisinopril 10 mg daily"})
    _narrative(m, "Started lisinopril 10 mg daily.",
               {"action": "ADD", "subject": "active_medication", "value": "lisinopril 10 mg daily"})
    (p,) = m.pending()                                                 # proposed once, not twice
    assert "lisinopril 10 mg daily" not in _current_meds(m)
    assert m.confirm(p.id, source="dr_okafor")[0] is Action.ADD
    assert "lisinopril 10 mg daily" in _current_meds(m) and "warfarin 5 mg daily" in _current_meds(m)
    assert m.confirm(p.id) is None                                     # already decided


def test_the_first_drug_on_an_empty_list_and_allergies_still_apply_at_once():
    m = LLMMemory()
    _narrative(m, "On warfarin 5 mg daily.", {"action": "ADD", "subject": "active_medication",
                                              "value": "warfarin 5 mg daily"})
    _narrative(m, "Allergic to sulfa.", {"action": "ADD", "subject": "allergy", "value": "sulfa"})
    _narrative(m, "Also allergic to latex.", {"action": "ADD", "subject": "allergy", "value": "latex"})
    assert _current_meds(m) == ["warfarin 5 mg daily"] and not m.pending()
    assert {s.value for s in m.active() if s.subject == "allergy"} == {"sulfa", "latex"}


# ── the sentence-level safety net (LLMMemory.add runs it after the extracted facts) ──

def _add(m, text, facts, monkeypatch):
    monkeypatch.setattr(llm_memory, "llm_extract_classify", lambda t, c: facts)
    return m.add(text)


def _queued(m):
    by_id = {s.id: s for s in m.statements}
    return sorted(((p.metadata or {}).get("pending"), by_id[p.supersedes].value if p.supersedes else p.value)
                  for p in m.pending())


def test_a_stop_filed_under_an_invented_subject_is_still_proposed(monkeypatch):
    """gpt-5-mini returned ARCHIVE warfarin_discontinuation = 2026-10-21: the stop never touched the
    medication list, so warfarin stayed current with nothing queued."""
    m = _chart()
    _add(m, "Warfarin was discontinued yesterday.", [
        {"action": "ARCHIVE", "subject": "warfarin_discontinuation", "value": "2026-10-21", "time_ref": "past"}],
        monkeypatch)
    assert _queued(m) == [("RETRACT", "warfarin 5 mg daily")]


def test_a_stop_archived_as_the_drug_itself_is_still_proposed(monkeypatch):
    m = _chart()
    _add(m, "Patient says she stopped taking her warfarin last week.", [
        {"action": "ARCHIVE", "subject": "active_medication", "value": "warfarin 5 mg daily", "time_ref": "past"}],
        monkeypatch)
    assert _queued(m) == [("RETRACT", "warfarin 5 mg daily")]


def test_switched_from_proposes_removing_the_old_drug_beside_the_new_one(monkeypatch):
    """gpt-6-luna returned only ADD apixaban; approving that alone would leave two anticoagulants."""
    m = _chart()
    _add(m, "Switched from warfarin to apixaban 5 mg BID.", [
        {"action": "ADD", "subject": "active_medication", "value": "apixaban 5 mg BID"}], monkeypatch)
    assert _queued(m) == [("ADD", "apixaban 5 mg BID"), ("RETRACT", "warfarin 5 mg daily")]


def test_the_sweep_names_only_the_drug_next_to_the_stop_and_never_doubles_a_proposal(monkeypatch):
    m = _chart()
    _add(m, "Stop warfarin, continue metformin.", [
        {"action": "RETRACT", "subject": "active_medication", "value": "warfarin 5 mg daily", "target_id": 1}],
        monkeypatch)
    assert _queued(m) == [("RETRACT", "warfarin 5 mg daily")]          # one proposal, and not metformin
    m2 = _chart()
    _add(m2, "We would probably stop the warfarin if her INR stays high.", [], monkeypatch)
    assert _queued(m2) == []                                           # a plan, not an order


def test_an_unknown_action_no_longer_loses_the_message(monkeypatch):
    m = _chart()
    _add(m, "Switch from warfarin to apixaban.", [
        {"action": "RETIRE", "subject": "active_medication", "value": "warfarin 5 mg daily", "target_id": 1},
        {"action": "ADD", "subject": "active_medication", "value": "apixaban 5 mg BID"},
        {"action": "NONSENSE", "subject": "code_status", "value": "DNR"}], monkeypatch)
    assert _queued(m) == [("ADD", "apixaban 5 mg BID"), ("RETRACT", "warfarin 5 mg daily")]
    assert next(s.value for s in m.active() if s.subject == "code_status") == "Full code"


# ── found by the held-out phrasings ──

def test_a_targeted_change_under_an_invented_subject_still_goes_through_the_gate(monkeypatch):
    """gpt-5-mini, 'Metformin held for contrast': SUPERSEDE metformin_therapy_status (target_id=2)
    retired metformin, and REFINE 'metformin 500 mg twice daily' (target_id=2) overwrote its value,
    both without approval, because the gate keyed on the invented subject."""
    for fact in ({"action": "SUPERSEDE", "subject": "metformin_therapy_status", "value": "metformin held for contrast",
                  "target_id": 2},
                 {"action": "REFINE", "subject": "metformin 500 mg twice daily", "value": "metformin held for contrast",
                  "target_id": 2}):
        m = _chart()
        _add(m, "Metformin held for contrast, resume in 48h.", [fact], monkeypatch)
        assert "metformin 500 mg twice daily" in _current_meds(m)
        assert all((p.metadata or {}).get("pending") for p in m.pending()) and m.pending()


def test_brand_names_match_the_generic_on_the_list(monkeypatch):
    """gpt-6-luna, 'Coumadin stopped, now on Eliquis': the stop didn't match 'warfarin 5 mg daily'."""
    m = _chart()
    _add(m, "Coumadin stopped, now on Eliquis 5 mg BID.", [
        {"action": "ADD", "subject": "active_medication", "value": "Eliquis 5 mg BID"},
        {"action": "ARCHIVE", "subject": "active_medication", "value": "Coumadin stopped", "time_ref": "past"}],
        monkeypatch)
    assert _queued(m) == [("ADD", "Eliquis 5 mg BID"), ("RETRACT", "warfarin 5 mg daily")]
    m2 = _chart()
    _add(m2, "She has been off warfarin since her bleed in March.", [
        {"action": "ARCHIVE", "subject": "active_medication", "value": "warfarin 5 mg daily", "time_ref": "past"}],
        monkeypatch)
    assert _queued(m2) == [("RETRACT", "warfarin 5 mg daily")]


def test_the_reviewer_is_warned_before_creating_two_anticoagulants():
    from vayl.memory.llm_memory import duplicate_anticoagulant
    m = _chart()
    _narrative(m, "Now on Eliquis.", {"action": "ADD", "subject": "active_medication", "value": "Eliquis 5 mg BID"})
    (add,) = m.pending()
    assert duplicate_anticoagulant(m, add) == ["warfarin 5 mg daily"]     # warfarin's stop wasn't captured
    _narrative(m, "Stop warfarin.", {"action": "RETRACT", "subject": "active_medication",
                                     "value": "warfarin 5 mg daily", "target_id": 1})
    assert duplicate_anticoagulant(m, add) == []                          # now its removal is queued too


def test_a_status_slug_is_never_proposed_as_a_medication(monkeypatch):
    """gpt-5-mini filed reasons and statuses as list values: 'REPLACE metformin -> held_for_contrast'
    and 'ADD acute_kidney_injury' reached the review queue."""
    m = _chart()
    _add(m, "Discontinue metformin due to AKI.", [
        {"action": "RETRACT", "subject": "active_medication", "value": "metformin 500 mg twice daily", "target_id": 2},
        {"action": "ADD", "subject": "active_medication", "value": "acute_kidney_injury"}], monkeypatch)
    assert _queued(m) == [("RETRACT", "metformin 500 mg twice daily")]
    m2 = _chart()
    _add(m2, "Metformin held for contrast, resume in 48h.", [
        {"action": "SUPERSEDE", "subject": "active_medication", "value": "held_for_contrast", "target_id": 2}],
        monkeypatch)
    assert _queued(m2) == [] and "metformin 500 mg twice daily" in _current_meds(m2)
    m3 = _chart()                                                      # a real entry is unaffected
    _add(m3, "Started insulin glargine 10 units nightly.", [
        {"action": "ADD", "subject": "active_medication", "value": "insulin glargine 10 units nightly"}], monkeypatch)
    assert _queued(m3) == [("ADD", "insulin glargine 10 units nightly")]


def test_a_stop_statement_that_names_no_drug_is_not_proposed_as_one(monkeypatch):
    m = _chart()
    _add(m, "She has been off warfarin since her bleed in March.", [
        {"action": "ADD", "subject": "active_medication", "value": "off since 2026-03"}], monkeypatch)
    assert _queued(m) == [("RETRACT", "warfarin 5 mg daily")]          # the sweep catches the real stop
    m2 = _chart()
    _add(m2, "Discontinue metformin due to AKI.", [
        {"action": "RETRACT", "subject": "active_medication", "value": "metformin 500 mg twice daily", "target_id": 2},
        {"action": "ADD", "subject": "active_medication", "value": "reason_discontinued: AKI"}], monkeypatch)
    assert _queued(m2) == [("RETRACT", "metformin 500 mg twice daily")]
