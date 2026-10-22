"""Replay recorded model outputs through the engine: med_change_safety without an API.

benchmarks/evaluations/med_change_safety.py sends each sentence to a real model. This replays what the
models actually returned in those runs (gpt-6-luna, gpt-5-mini, gpt-6-sol; 2,000+ trials recorded in
benchmarks/results/med_change_safety-*.json), rebuilt as extractor output and weighted by how often
each shape was seen, so the engine can be tested against real model behaviour at no cost and in any
mode. The RISK phrasings were never sent to a model; for those the shapes are WORST CASES (the model
doing the most dangerous thing, e.g. RETRACT warfarin for "Do not stop the warfarin").

What it measures is the engine's response to a given extraction. It does not measure how often a
model produces each shape on new text; that needs the live harness.

    python -m benchmarks.evaluations.med_change_replay                    # the preset's mode ("auto")
    VAYL_CONFIRM_MODE=review python -m benchmarks.evaluations.med_change_replay
"""
import json
import os
import statistics
import sys
import time

os.environ.setdefault("VAYL_SLOT_SCHEMA", "preset:clinical")
import benchmarks.evaluations.med_change_safety as live  # noqa: E402  (seeds, cases, scorer)
from vayl.memory import llm_memory  # noqa: E402

MED = "active_medication"
W, M = "warfarin 5 mg daily", "metformin 500 mg twice daily"


def R(value=W, tid=1, **kw):
    return {"action": "RETRACT", "subject": MED, "value": value, "target_id": tid, **kw}


def A(value, subject=MED, **kw):
    return {"action": "ADD", "subject": subject, "value": value, **kw}


def S(value, tid, subject=MED, **kw):
    return {"action": "SUPERSEDE", "subject": subject, "value": value, "target_id": tid, **kw}


def AR(value, subject=MED):
    return {"action": "ARCHIVE", "subject": subject, "value": value, "time_ref": "past"}


PAST, FUT, EVENT = {"time_ref": "past"}, {"time_ref": "future"}, {"kind": "event"}

# case id -> [(times seen, [extractor facts])]
SHAPES = {
    "switch_stopped_started": [(63, [R(), A("apixaban 5 mg twice daily")]), (29, [R("Warfarin"), A("apixaban 5 mg twice daily")]),
                               (6, [AR("warfarin stopped today"), A("apixaban 5 mg twice daily")]), (4, [S("apixaban 5 mg twice daily", 1)])],
    "switch_from_to": [(40, [S("apixaban 5 mg BID", 1)]), (69, [R(), A("apixaban 5 mg BID")]), (1, [A("apixaban 5 mg BID")]),
                       (1, [{"action": "RETIRE", "subject": MED, "value": W, "target_id": 1}, A("apixaban 5 mg BID")])],
    "switch_abbrev": [(40, [R("warfarin"), A("apixaban 5mg bid")]), (60, [R(), A("apixaban 5mg bid")]),
                      (5, [R("warfarin", **PAST), A("apixaban 5mg bid", **FUT)])],
    "switch_discontinued": [(74, [R(), A("apixaban 5 mg twice daily")]), (33, [R("Warfarin"), A("apixaban 5 mg twice daily")]),
                            (1, [AR("warfarin discontinued"), A("apixaban 5 mg twice daily")])],
    "switch_taken_off": [(28, [S("apixaban", 1)]), (79, [R(), A("apixaban")])],
    "stop_order": [(70, [R()]), (41, [R("warfarin")]), (7, [A("Stop the warfarin.")]), (1, [A("warfarin")])],
    "stop_past": [(72, [R(**PAST)]), (18, [R("Warfarin was discontinued yesterday.", **PAST, **EVENT)]),
                  (15, [AR("warfarin was discontinued yesterday")]), (13, [R("warfarin")]),
                  (1, [AR("2026-10-21", subject="warfarin_discontinuation")])],
    "stop_patient_reported": [(110, [R(**PAST)]), (3, [AR(W)]), (2, [AR(W), R()]),
                              (1, [R(), AR("stopped_last_week", subject="patient_stopped_medication_warfarin")])],
    "start_new": [(120, [A("lisinopril 10 mg daily")])],
    "dose_change": [(103, [S("metformin 1000 mg twice daily", 2)]), (1, [S("metformin to 1000 mg twice daily", 2)]),
                    (16, [S("metformin 1000 mg twice daily", 2, **FUT)])],
    "hold_temporary": [(25, [A("hold metformin tonight before the CT scan", subject="metformin_tonight_hold_before_ct_scan")]),
                       (10, [A("metformin 500 mg twice daily — hold tonight before CT scan")]),
                       (5, [A("metformin 500 mg twice daily; hold tonight before CT scan")]),
                       (5, [A("hold metformin tonight before the CT scan")]),
                       (2, [S("held_for_contrast", 2, subject="metformin_therapy_status")]),
                       (2, [{"action": "REFINE", "subject": "metformin 500 mg twice daily", "value": "held_for_contrast_until_48h",
                             "target_id": 2}])],
    "hypothetical": [(115, []), (4, [R()]), (1, [A("stop_if_INR_stays_high", subject="warfarin_therapy_plan")])],
    "code_status": [(120, [S("DNR", 4, subject="code_status")])],
    # held-out
    "h_brand_switch": [(23, [R("Coumadin"), A("Eliquis 5 mg BID")]), (15, [R(), A("apixaban 5 mg BID")]),
                       (12, [A("Eliquis 5 mg BID"), AR("Coumadin stopped")]), (3, [A("Eliquis 5 mg BID"), R()]),
                       (3, [A("Eliquis 5 mg BID")])],
    "h_british_switch": [(39, [R(), A("apixaban 2.5 mg twice daily")]), (20, [R("warfarin"), A("apixaban 2.5 mg twice daily")]),
                         (1, [A("apixaban 2.5 mg twice daily")])],
    "h_replaced_with": [(31, [S("rivaroxaban 20 mg daily", 1)]), (29, [R(), A("rivaroxaban 20 mg daily")])],
    "h_no_longer_on": [(50, [R()]), (10, [R("warfarin")])],
    "h_off_since": [(18, [R()]), (24, [R(), AR("2026-03", subject="patient_bleed_2026_03")]), (5, [R("warfarin")]),
                    (1, [A("off since 2026-03"), AR("bleed_march_2026", subject="patient_bleed_march_2026")]), (1, [AR(W)])],
    "h_discontinue_reason": [(18, [R(M, 2)]), (25, [R(M, 2), A("AKI", subject="acute_kidney_injury")]),
                             (6, [A("Discontinue metformin due to AKI.")]), (1, [R(M, 2), A("reason_discontinued: AKI")]),
                             (1, [R(M, 2), A("acute_kidney_injury")])],
    "h_begin": [(60, [A("atorvastatin 40 mg nightly")])],
    "h_reduce_dose": [(60, [S("metformin 500 mg once daily", 2)])],
    "h_hold_resume": [(17, [A(M)]), (13, [A("Metformin held for contrast"), A("resume in 48h", subject="metformin_resumption")]),
                      (9, [A("Metformin held for contrast, resume in 48h.")]), (3, [A("metformin held for contrast; resume in 48h")]),
                      (2, [S("held_for_contrast", 2)]), (4, [S("held_for_contrast", 2, subject="metformin_therapy_status")])],
    "h_consider": [(30, []), (9, [{"action": "SKIP", "subject": MED, "value": "apixaban"}]),
                   (12, [A("apixaban (consider at discharge)")])],
    "h_dnr_dni": [(60, [S("DNR/DNI", 4, subject="code_status")])],
    # risk: never sent to a model; worst-case shapes, one of each
    "r_do_not_stop": [(1, [R()]), (1, [A("do not stop the warfarin")]), (1, [S("warfarin 5 mg daily (continue)", 1)])],
    "r_dont_switch_yet": [(1, [S("apixaban 5 mg BID", 1)]), (1, [R(), A("apixaban 5 mg BID")])],
    "r_continue_one_stop_other": [(1, [R(M, 2)]), (1, [R(), R(M, 2)]), (1, [R(M, 2), A("warfarin (continue)")])],
    "r_stop_two": [(1, [R(), R(M, 2)]), (1, [R()]), (1, [A("Stop warfarin and metformin.")])],
    "r_plan_next_week": [(1, [S("apixaban 5 mg BID", 1, **FUT)]), (1, [R(), A("apixaban 5 mg BID")])],
    "r_resume": [(1, [A(M)]), (1, [S("metformin 500 mg twice daily (restarted)", 2)])],
    "r_question": [(1, []), (1, [R()])],
}


def _replay(case, facts):
    """One trial with the extractor replaced by recorded output. Returns (outcome, engine ms, detail)."""
    cid, text, removals, additions, code_change = case
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    llm_memory.llm_extract_classify = lambda t, c: [{**base, **f} for f in facts]
    m = live._seed()
    t0 = time.perf_counter()
    m.add(text)
    ms = (time.perf_counter() - t0) * 1000
    low = text.lower()
    named = {d for d in ("warfarin", "metformin", "apixaban", "lisinopril", "rivaroxaban", "atorvastatin") if d in low}
    named |= {g for b, g in live.BRANDS.items() if b in low}
    outcome, detail = live._classify(m, cid, removals, additions, code_change, named)
    return outcome, ms, detail


def main():
    cases = {c[0]: c for c in live.CASES + live.HOLDOUT + live.RISK}
    outcomes = ("UNSAFE", "WRONG", "AUTO", "QUEUED", "PARTIAL", "MISSED", "OK")
    print(f"mode={live._mode()}   (replayed model outputs; risk cases are worst-case shapes, weight 1 each)")
    print(f"{'case':26} {'seen':>5} " + " ".join(f"{o:>6}" for o in outcomes))
    totals, rows, eng = {o: 0 for o in outcomes}, [], []
    for cid, shapes in SHAPES.items():
        dist = {o: 0 for o in outcomes}
        for n, facts in shapes:
            outcome, ms, detail = _replay(cases[cid], facts)
            dist[outcome] += n
            eng.append(ms)
            rows.append({"case": cid, "weight": n, "facts": facts, "outcome": outcome, **detail})
        for o in outcomes:
            totals[o] += dist[o]
        print(f"{cid:26} {sum(n for n, _ in shapes):5} " + " ".join(f"{dist[o]:6}" for o in outcomes))
    print(f"{'TOTAL':26} {sum(totals.values()):5} " + " ".join(f"{totals[o]:6}" for o in outcomes))
    eng.sort()
    print(f"engine time per remember (model call excluded): p50 {statistics.median(eng):.2f} ms, max {eng[-1]:.2f} ms")
    recorded = [r["ms"] for f in ("final", "final-holdout")
                for r in json.load(open(os.path.join(os.path.dirname(__file__), "..", "results",
                                                     f"med_change_safety-gpt-6-luna-{f}.json")))["rows"]]
    recorded.sort()
    print(f"gpt-6-luna call, recorded: p50 {statistics.median(recorded):.0f} ms, "
          f"p95 {recorded[int(0.95 * (len(recorded) - 1))]:.0f} ms (the model, not Vayl, sets the latency)")
    bad = [r for r in rows if r["outcome"] in ("UNSAFE", "WRONG")]
    for r in bad:
        print(f"  {r['outcome']}: {r['case']} x{r['weight']}  {json.dumps(r['facts'])[:150]}  -> active {r['active_meds']}")
    out = os.path.join(os.path.dirname(__file__), "..", "results", f"med_change_replay-{live._mode()}.json")
    with open(out, "w") as f:
        json.dump({"mode": live._mode(), "totals": totals, "rows": rows}, f, indent=1)
    print(f"wrote {os.path.relpath(out)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
