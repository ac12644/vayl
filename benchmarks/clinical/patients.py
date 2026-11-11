"""
Synthetic longitudinal patient records — realistic, HIPAA-safe, and built to stress every clinical
guarantee Vayl added.

These are fictional patients. Names, MRNs and dates are invented; the clinical content (drugs, doses,
frequencies, the shape of an admission) is realistic. A production hospital integration would feed
facts like these from an EHR / FHIR event stream, already structured — extraction from free text is
the model's job and the weak link, and is deliberately not what this dataset tests. What it tests is
the deterministic reconciliation and safety machinery: one current value per medication, allergies
that never fall out of retrieval, medication stops and code-status changes that wait for a human,
and clinical events that coexist instead of overwriting one another.

Each record is an ordered list of facts. A fact is a dict shaped like the extractor's output, so it
runs through the exact same `_apply` path a real ingest would — only the extraction step is skipped.

Fields:
  subject   the slot. Some are deliberately named inconsistently across a record (allergy vs
            patient_allergy vs drug_allergy) to exercise schema aliasing.
  value     verbatim clinical value — doses and units must survive intact.
  kind      "state" (holds until changed) or "event" (happened once; must coexist).
  action    ADD / SUPERSEDE / RETRACT, as the source system would assert it.
  note      the source utterance, kept as `raw`.
  day       relative admission day, for readability in the report.
"""


def _f(subject, value, note, kind="state", action="ADD", day=0, conf=0.95,
       scope="global", time_ref="present", source="ehr_feed"):
    return {"subject": subject, "value": value, "kind": kind, "action": action,
            "scope": scope, "time_ref": time_ref, "confidence": conf, "note": note, "day": day,
            "source": source}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 1 — Margaret Chen, 72F. CAP admission on chronic afib + T2DM.
# Exercises: allergy always-retrievable, medication reconcile, warfarin hold (confirm),
# code-status change (confirm), dose change (reconcile), a fall and a procedure (events),
# and the same allergy recorded three ways (aliasing).
# ─────────────────────────────────────────────────────────────────────────────

MARGARET = {
    "patient_id": "mrn_4471_margaret_chen",
    "summary": "72F, community-acquired pneumonia, chronic AF, type 2 diabetes",
    "facts": [
        # ── admission (day 0) ──
        _f("patient_allergy", "penicillin — anaphylaxis (throat swelling, 2019)",
           "Allergy: penicillin, anaphylaxis per patient and prior chart.", day=0),
        _f("allergy", "sulfa drugs — rash",
           "Also reports sulfa rash.", day=0),                      # alias -> same slot vocab
        _f("active_medication", "warfarin 5 mg PO daily",
           "Home warfarin 5mg daily for AF.", day=0),                # med LIST member
        _f("active_medication_metformin", "metformin 500 mg PO BID",
           "Metformin 500 BID for T2DM.", day=0),                    # titrated drug: own slot
        _f("code_status", "Full code",
           "Code status confirmed full on admission.", day=0),
        _f("primary_diagnosis", "community-acquired pneumonia, right lower lobe",
           "CXR: RLL consolidation. Dx CAP.", day=0),
        _f("care_team_lead", "Dr. Alan Reyes, hospitalist",
           "Attending: Dr. Reyes.", day=0),

        # ── day 1: start CAP therapy (penicillin-allergic, so a fluoroquinolone) ──
        _f("active_medication", "levofloxacin 750 mg IV daily",
           "Started levofloxacin 750 IV daily — penicillin-allergic, avoid beta-lactam.", day=1),

        # ── day 2: INR 4.8, warfarin HELD. removal on a confirm slot -> proposal ──
        _f("active_medication", "warfarin 5 mg PO daily",
           "INR 4.8, supratherapeutic. Hold warfarin.", kind="state", action="RETRACT", day=2),

        # ── day 2: fall in the bathroom. EVENT — must not overwrite anything ──
        _f("fall_event_day2", "unwitnessed fall in bathroom, no head strike, no injury",
           "Pt found on bathroom floor, denies head strike, neuro intact.",
           kind="event", day=2),

        # ── day 3: the same allergy re-entered under a third subject name (aliasing) ──
        _f("drug_allergy", "penicillin — anaphylaxis",
           "Nursing re-confirmed penicillin anaphylaxis at med rec.", day=3),

        # ── day 3: metformin dose increased. single-slot SUPERSEDE — one current value ──
        _f("active_medication_metformin", "metformin 1000 mg PO BID",
           "Uptitrated metformin to 1000 BID, A1c 8.9%.", action="SUPERSEDE", day=3),

        # ── day 4: family meeting, goals of care. code status change -> proposal ──
        _f("code_status", "DNR / DNI",
           "Family meeting: transition to DNR/DNI per patient's prior wishes.",
           action="SUPERSEDE", day=4),

        # ── day 4: PICC line placed. EVENT ──
        _f("procedure_day4", "PICC line placed, right basilic, tip confirmed by CXR",
           "PICC placed R basilic vein, tip at cavoatrial junction on CXR.",
           kind="event", day=4),

    ],
    # what a clinician must be able to recover at discharge, and what must be true
    "expectations": {
        "critical_present": ["penicillin", "sulfa"],          # BOTH allergies coexist + reachable
        "one_current_metformin": "metformin 1000 mg PO BID",
        "events_coexist": ["fall_event_day2", "procedure_day4"],
        "pending_awaiting_review": 3,                          # warfarin hold, code-status -> DNR, and
        # the levofloxacin start: a new drug joining a non-empty med list from the (untrusted)
        # ehr_feed waits for approval too, so a misread order can never add an active drug
        # a proposed medication STOP must not remove the drug until approved: warfarin stays current
        # while its removal waits. a proposed code-status CHANGE is the opposite: DNR is not current
        # until approved, full code stands.
        "held_still_current": ["warfarin"],
        "proposed_not_current": ["DNR", "levofloxacin"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 2 — James Okoro, 58M. Post-op sepsis. Rapid medication churn.
# Exercises: heavy medication reconciliation, a pressor titration (events), an allergy
# discovered mid-stay, an antibiotic de-escalation.
# ─────────────────────────────────────────────────────────────────────────────

JAMES = {
    "patient_id": "mrn_8823_james_okoro",
    "summary": "58M, post-op day 3 sigmoid colectomy, septic shock",
    "facts": [
        _f("patient_allergy", "no known drug allergies",
           "NKDA documented pre-op.", day=0),
        _f("active_medication_abx", "piperacillin-tazobactam 4.5 g IV q6h",
           "Empiric pip-tazo for intra-abdominal source.", day=0),   # own slot: de-escalated by dose
        _f("code_status", "Full code", "Full code.", day=0),
        _f("primary_diagnosis", "septic shock, intra-abdominal source",
           "Septic shock, presumed anastomotic leak.", day=0),

        # allergy DISCOVERED mid-stay. It is a LIST ADD -> immediate, never queued: surfacing a
        # newly found allergy is safety-positive. The prior NKDA note is retracted.
        _f("patient_allergy", "no known drug allergies",
           "NKDA note retired — allergy found.", action="RETRACT", day=1),
        _f("patient_allergy", "vancomycin — red man syndrome (flushing, hypotension)",
           "Developed flushing + hypotension with vanc infusion — red man, slow rate / switch.",
           day=1),

        # pressor titrations — EACH an event (a titration happened), values coexist
        _f("pressor_event_norepi_start", "norepinephrine started 0.05 mcg/kg/min",
           "Norepi started 0.05 for MAP<65.", kind="event", day=0),
        _f("pressor_event_norepi_up", "norepinephrine up-titrated to 0.20 mcg/kg/min",
           "Norepi up to 0.20, MAP 58.", kind="event", day=0),
        _f("pressor_event_vaso_add", "vasopressin 0.03 units/min added",
           "Added vasopressin 0.03 as second pressor.", kind="event", day=1),

        # antibiotic de-escalation after cultures — reconcile (one current abx line)
        _f("active_medication_abx", "meropenem 1 g IV q8h",
           "ESBL E. coli on culture — escalate to meropenem.", action="SUPERSEDE", day=2),

        # weaned off pressors — the pressor STATE (currently on pressors?) retracts; events stay
        _f("hemodynamic_support", "on two vasopressors",
           "On norepi + vasopressin.", day=1),
        _f("hemodynamic_support", "on two vasopressors",
           "Weaned off all pressors, MAP stable off support.", action="RETRACT", day=3),
    ],
    "expectations": {
        "critical_present": ["vancomycin", "red man"],       # discovered allergy, immediate
        "one_current_abx": "meropenem 1 g IV q8h",
        "events_coexist": ["pressor_event_norepi_start", "pressor_event_norepi_up",
                           "pressor_event_vaso_add"],
        "pending_awaiting_review": 0,     # abx slot is its own single slot, not confirm-gated
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 3 — Rosa Delgado, 34F. Pregnancy, brief obs stay. Mostly clean, one
# fragmentation stress: the same allergy entered under four different subject spellings.
# ─────────────────────────────────────────────────────────────────────────────

ROSA = {
    "patient_id": "mrn_2093_rosa_delgado",
    "summary": "34F, 28 weeks pregnant, hyperemesis, 23h observation",
    "facts": [
        _f("allergy", "codeine — nausea and vomiting",
           "Allergy codeine, N/V.", day=0),
        _f("patient_allergy", "codeine — nausea and vomiting",
           "Codeine allergy re-entered by triage nurse.", day=0),
        _f("drug_allergy", "codeine — nausea and vomiting",
           "Codeine allergy on the med-rec form.", day=0),
        _f("known_allergies", "codeine — nausea and vomiting",
           "Codeine noted again at pharmacy review.", day=0),
        _f("active_medication", "ondansetron 4 mg IV q8h PRN",
           "Ondansetron 4 IV q8h PRN nausea.", day=0),
        _f("primary_diagnosis", "hyperemesis gravidarum, 28 weeks gestation",
           "Hyperemesis at 28w.", day=0),
        _f("code_status", "Full code", "Full code.", day=0),
    ],
    "expectations": {
        "critical_present": ["codeine"],
        "allergy_slots_after_aliasing": 1,      # four spellings -> one canonical allergy slot
        "pending_awaiting_review": 0,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 4 — Kwame Asante, 41M. DKA, long ICU stay. VOLUME + CHURN + WORKFLOW.
# Exercises: the critical-fact channel under real ranking pressure (an allergy entered on
# day 0 must still surface after ~40 facts), rapid supersession of one slot (insulin drip
# titrated eight times → one current value, full history), and the confirmation WORKFLOW
# actually run (a code-status change is proposed AND then confirmed, not just queued).
# ─────────────────────────────────────────────────────────────────────────────

KWAME = {
    "patient_id": "mrn_5510_kwame_asante",
    "summary": "41M, DKA, 6-day ICU stay, insulin drip + electrolyte repletion",
    "facts": [
        # early, safety-critical — must still be reachable at the end under all the noise below
        _f("patient_allergy", "iodinated contrast — anaphylactoid reaction", "Contrast allergy.", day=0),
        _f("code_status", "Full code", "Full code on admission.", day=0),
        _f("primary_diagnosis", "diabetic ketoacidosis, anion gap 28", "DKA, gap 28.", day=0),
        _f("care_team_lead", "Dr. Priya Nair, intensivist", "Attending: Dr. Nair.", day=0),
        _f("active_medication", "insulin glargine 20 units SC nightly", "Home glargine 20u.", day=0),

        # insulin drip titrated to glucose — one slot, eight distinct values, only last is current
        _f("insulin_drip_rate", "insulin infusion 2 units/hr", "Drip start 2 u/hr.", day=0),
        _f("insulin_drip_rate", "insulin infusion 4 units/hr", "Glc 480, up to 4.", action="SUPERSEDE", day=0),
        _f("insulin_drip_rate", "insulin infusion 6 units/hr", "Glc 445, up to 6.", action="SUPERSEDE", day=0),
        _f("insulin_drip_rate", "insulin infusion 5 units/hr", "Glc 320, down to 5.", action="SUPERSEDE", day=1),
        _f("insulin_drip_rate", "insulin infusion 3 units/hr", "Glc 240, down to 3.", action="SUPERSEDE", day=1),
        _f("insulin_drip_rate", "insulin infusion 2 units/hr", "Glc 180, down to 2.", action="SUPERSEDE", day=1),
        _f("insulin_drip_rate", "insulin infusion 1 unit/hr", "Glc 150, down to 1.", action="SUPERSEDE", day=2),
        _f("insulin_drip_rate", "insulin infusion off, transition to SC", "Gap closed, off drip.",
           action="SUPERSEDE", day=2),

        # electrolyte repletions — EACH an event; a DKA course has many, they all coexist
        _f("k_repletion_1", "KCl 40 mEq IV given", "K 3.1, repleted 40.", kind="event", day=0),
        _f("k_repletion_2", "KCl 20 mEq IV given", "K 3.4, repleted 20.", kind="event", day=0),
        _f("k_repletion_3", "KCl 40 mEq IV given", "K 3.0, repleted 40.", kind="event", day=1),
        _f("phos_repletion_1", "sodium phosphate 15 mmol IV given", "Phos 1.1, repleted.", kind="event", day=1),
        _f("mag_repletion_1", "magnesium sulfate 2 g IV given", "Mg 1.4, repleted.", kind="event", day=1),

        # background monitoring facts — realistic volume the allergy must survive being ranked against
        _f("iv_fluids", "0.9% saline at 250 mL/hr", "NS 250/hr.", day=0),
        _f("iv_fluids", "0.45% saline at 150 mL/hr with D5", "Switched to half-NS + D5.",
           action="SUPERSEDE", day=1),
        _f("diet_status", "NPO", "NPO for now.", day=0),
        _f("diet_status", "clear liquids, carb-counted", "Advanced to clears.", action="SUPERSEDE", day=2),
        _f("primary_diagnosis", "diabetic ketoacidosis, resolved; poorly controlled T2DM",
           "DKA resolved, gap closed.", action="SUPERSEDE", day=3),

        # day 3: precautionary code-status conversation — PROPOSED (confirm slot)
        _f("code_status", "DNR, full treatment otherwise", "Pt requests DNR, wants full care else.",
           action="SUPERSEDE", day=3),
    ],
    # this record's proposals are RESOLVED, then the resolved state is checked
    "workflow": [
        {"confirm": "code_status", "by": "dr_nair"},   # the DNR change is approved
    ],
    "expectations": {
        "critical_present": ["iodinated contrast", "anaphylactoid"],   # day-0 allergy, ~40 facts later
        "one_current_insulin_drip": "insulin infusion off, transition to SC",
        "insulin_drip_history_min": 7,                 # the earlier rates preserved as history
        "events_coexist": ["k_repletion_1", "k_repletion_2", "k_repletion_3",
                           "phos_repletion_1", "mag_repletion_1"],
        # after the workflow confirms the DNR, it becomes current and nothing is left pending
        "after_workflow_current": ["DNR"],
        "after_workflow_pending": 0,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 5 — Ingrid Larsson, 67F. Sources DISAGREE. The FLAG path.
# Exercises: a genuine unresolved conflict on a single-valued slot that must surface as
# disputed rather than silently pick a side; scope-based coexistence (a drug used for two
# different indications at two doses); and a low-confidence change that must not auto-apply.
# ─────────────────────────────────────────────────────────────────────────────

INGRID = {
    "patient_id": "mrn_7314_ingrid_larsson",
    "summary": "67F, syncope workup, conflicting outside records",
    "facts": [
        _f("patient_allergy", "no known drug allergies", "NKDA per patient.", day=0),
        _f("code_status", "Full code", "Full code.", day=0),

        # two services assert DIFFERENT baseline weights, neither clearly newer, low confidence.
        # a weight drives drug dosing — a silent pick is unsafe; this must FLAG.
        _f("dosing_weight", "72 kg", "ED recorded 72 kg.", conf=0.6, day=0),
        _f("dosing_weight", "81 kg", "Outside record lists 81 kg.", conf=0.6, action="FLAG", day=0),

        # aspirin used for TWO indications at TWO doses — different SCOPE, both true (COEXIST)
        _f("aspirin_regimen", "aspirin 81 mg PO daily", "ASA 81 for cardioprotection.",
           scope="cardiac", day=0),
        _f("aspirin_regimen", "aspirin 325 mg PO once", "ASA 325 loading for suspected ACS.",
           scope="acute", day=1),

        # a hedged medication change — low confidence, must not auto-apply (honest-uncertainty)
        _f("active_medication", "metoprolol 25 mg PO BID", "Home metoprolol 25 BID.", day=0),
        _f("active_medication", "metoprolol 50 mg PO BID",
           "?maybe uptitrate metoprolol, unclear from notes.", conf=0.4, action="SUPERSEDE", day=1),
    ],
    "expectations": {
        # the weight conflict is surfaced, not silently resolved to one value
        "flagged_subject_present": "dosing_weight",
        "flagged_both_values": ["72 kg", "81 kg"],
        # aspirin at two scopes coexists — both regimens are real
        "scope_coexist_subject": "aspirin_regimen",
        "scope_coexist_count": 2,
        # the low-confidence metoprolol change did NOT overwrite the home dose
        "not_overwritten": ("active_medication", "metoprolol 25 mg PO BID"),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 6 — Ted Bradford, 78M. Code-status reversal + discharge + clinical HISTORY.
# Exercises: a value reversed through the confirmation workflow (full → DNR → back to full),
# the valid-time gate (past medical history must be recorded as HISTORY, never become an
# active current problem), and discharge medication reconciliation (a held inpatient drug
# and a resumed home drug).
# ─────────────────────────────────────────────────────────────────────────────

TED = {
    "patient_id": "mrn_9902_ted_bradford",
    "summary": "78M, CHF exacerbation, goals-of-care changed twice",
    "facts": [
        _f("patient_allergy", "ACE inhibitors — angioedema", "ACEi angioedema, avoid.", day=0),
        _f("code_status", "Full code", "Full code on admission.", day=0),
        _f("primary_diagnosis", "acute decompensated heart failure", "ADHF.", day=0),

        # PAST medical history — must be archived as history, NOT become a current active problem.
        # The valid-time gate handles time_ref=past.
        _f("cardiac_history", "myocardial infarction in 2019, stented LAD",
           "History of MI 2019 s/p LAD stent.", time_ref="past", day=0),
        _f("cardiac_history", "prior smoker, quit 2015",
           "Ex-smoker, quit 2015.", time_ref="past", day=0),

        _f("active_medication", "furosemide 40 mg IV BID", "IV diuresis.", day=0),
        _f("active_medication", "metoprolol 25 mg PO daily", "Home metoprolol continued.", day=0),

        # family meeting 1: change to DNR (proposed) — then confirmed via workflow
        _f("code_status", "DNR / DNI", "Family meeting 1: DNR/DNI.", action="SUPERSEDE", day=2),
        # family meeting 2: patient improves, REVERSES back to full code (proposed) — then confirmed
        _f("code_status", "Full code", "Family meeting 2: pt improved, reverse to full code.",
           action="SUPERSEDE", day=4),

        # discharge: stop IV diuretic, resume the oral home dose (a routine med-rec swap)
        _f("active_medication", "furosemide 40 mg IV BID", "D/C IV furosemide.",
           action="RETRACT", day=5),
        _f("active_medication", "furosemide 40 mg PO daily", "Discharge on PO furosemide.", day=5),
    ],
    "workflow": [
        # the DNR is approved on day 3 — AFTER it is proposed (day 2), BEFORE the reversal is
        # proposed (day 4). Only then is DNR the current value a reversal can supersede.
        {"confirm": "code_status", "by": "dr_okafor", "day": 3},
        # the reversal back to full code is approved on day 5
        {"confirm": "code_status", "by": "dr_okafor", "day": 5},
    ],
    "expectations": {
        "critical_present": ["ACE inhibitor", "angioedema"],
        # past history is archived, never presented as a current active fact
        "history_not_current": ["myocardial infarction in 2019", "prior smoker"],
        # after both approvals, code status is back to full (the reversal completed)
        "after_workflow_current": ["Full code"],
        # left for pharmacy: the discharge IV-furosemide retract, the PO furosemide that would replace
        # it, and metoprolol (a second drug joining the admission list) — every change to a non-empty
        # med list from the untrusted ehr_feed waits for approval
        "after_workflow_pending": 3,
        # discharge med rec: IV furosemide stays current until a person approves the switch
        "held_still_current": ["furosemide 40 mg IV BID"],
        "proposed_not_current": ["metoprolol", "furosemide 40 mg PO daily"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Patient 7 — Dorothy Feldman, 81F. NSTEMI. Built for DISCHARGE MED RECONCILIATION.
# Home medication history (BPMH) vs inpatient changes → every med-rec category:
# continued, changed, stopped, new, and one held for pharmacy review.
# ─────────────────────────────────────────────────────────────────────────────

DOROTHY = {
    "patient_id": "mrn_6640_dorothy_feldman",
    "summary": "81F, NSTEMI, discharge medication reconciliation",
    "facts": [
        _f("patient_allergy", "penicillin — hives", "PCN hives.", day=0),

        # ── HOME medications (Best Possible Medication History) — source=bpmh ──
        _f("active_medication", "metoprolol tartrate 25 mg PO BID",
           "Home metoprolol 25 BID.", day=0, source="bpmh"),
        _f("active_medication", "atorvastatin 20 mg PO nightly",
           "Home atorvastatin 20.", day=0, source="bpmh"),
        _f("active_medication", "lisinopril 10 mg PO daily",
           "Home lisinopril 10.", day=0, source="bpmh"),
        _f("active_medication", "furosemide 20 mg PO daily",
           "Home furosemide 20.", day=0, source="bpmh"),

        # ── inpatient course — source=inpatient (an authorized feed: applies directly) ──
        # CHANGED: statin uptitrated for ACS. SUPERSEDE targets the home atorvastatin by identity.
        _f("active_medication", "atorvastatin 80 mg PO nightly",
           "High-intensity statin post-NSTEMI: atorvastatin 80.", day=1,
           action="SUPERSEDE", source="inpatient"),

        # STOPPED: ACE inhibitor held for AKI
        _f("active_medication", "lisinopril 10 mg PO daily",
           "Hold lisinopril — creatinine bumped, AKI.", action="RETRACT", day=2, source="inpatient"),

        # NEW: dual antiplatelet + anticoagulant started
        _f("active_medication", "aspirin 81 mg PO daily",
           "ASA 81 started.", day=1, source="inpatient"),
        _f("active_medication", "clopidogrel 75 mg PO daily",
           "Clopidogrel started, DAPT post-PCI.", day=1, source="inpatient"),

        # HELD: a discharge PHARMACY DRAFT proposes consolidating metoprolol to daily dosing.
        # A draft is not an authorized order (untrusted source), so it is gated for verification —
        # the metoprolol stays at its home dose until pharmacy signs off. Same drug identity, so it
        # targets the home tartrate rather than adding a second metoprolol.
        _f("active_medication", "metoprolol tartrate 50 mg PO daily",
           "Pharmacy draft: consolidate metoprolol to 50 mg daily — verify before discharge.",
           action="SUPERSEDE", day=3, source="pharmacy_draft"),
    ],
    "expectations": {
        "critical_present": ["penicillin"],
    },
}


PATIENTS = [MARGARET, JAMES, ROSA, KWAME, INGRID, TED, DOROTHY]
