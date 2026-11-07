"""Does Vayl tell a CORRECTION of an auto-applied write from a LEGITIMATE later change? No model calls:
each scenario auto-applies one change, then a follow-up write arrives, labelled by hand as a correction
(the auto-apply was wrong) or a real change (the world moved on). Compared with the naive signal from
the revert-rate literature: any later write that touches an auto-applied write counts as its error.

    python -m benchmarks.evaluations.correction_attribution
"""
import os
import time

os.environ.setdefault("VAYL_SLOT_SCHEMA", "preset:clinical")
os.environ.setdefault("VAYL_TRUSTED_SOURCES", "fhir")
from vayl.memory import llm_memory  # noqa: E402
from vayl.memory.llm_memory import LLMMemory  # noqa: E402

MED = "active_medication"
DAY = 86400
DRUGS = [("warfarin", "5 mg daily", "7.5 mg daily", "10 mg daily"),
         ("metformin", "500 mg twice daily", "1000 mg twice daily", "850 mg twice daily"),
         ("lisinopril", "10 mg daily", "20 mg daily", "40 mg daily"),
         ("atorvastatin", "20 mg nightly", "40 mg nightly", "80 mg nightly"),
         ("levothyroxine", "50 mcg daily", "75 mcg daily", "100 mcg daily")]

# (name, is_correction, days after the auto-apply, follow-up kind, follow-up text). Kinds: "back" to the
# exact prior value, "third" value, "edit" through update(), "stop" the auto-added drug, "readd" a drug
# the auto-apply stopped. The auto-apply is a dose change except for "stop" (a start) and "readd" (a stop).
SCENARIOS = [
    ("reversed next day",                   True,  1,  "back",  "{d} {a}."),
    ("corrected by note, new value",        True,  2,  "third", "Correction: {d} is {c}, the {b} was entered wrong."),
    ("'still on' the old dose",             True,  1,  "back",  "No, she is still on {d} {a}."),
    ("clinician edits the entry",           True,  0,  "edit",  ""),
    ("auto-started drug removed same day",  True,  0,  "stop",  "Remove {d}, that was a mistake."),
    ("auto-stop undone by the feed",        True,  1,  "readd", "{d} {a}."),
    ("titrated up a week later",            False, 9,  "third", "Increase {d} to {c}."),
    ("titrated up two days later",          False, 2,  "third", "Increase {d} to {c}."),
    ("stopped a month later",               False, 30, "stop",  "Stop {d}."),
    ("restarted a month later",             False, 30, "readd", "Restart {d} {a}."),
    ("dose reduced back after side effect", False, 3,  "back",  "Reduce {d} back to {a} after the bleed."),   # hard
]


def _say(m, text, *facts, source=""):
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    llm_memory.llm_extract_classify = lambda t, c: [{**base, **f} for f in facts]
    llm_memory.llm_verdict = lambda *a, **k: []   # recordings hold the first pass only
    m.add(text, source=source)


def _run(drug, a, b, c, days, kind, text):
    m = LLMMemory()
    _say(m, "seed", {"action": "ADD", "subject": MED, "value": "aspirin 81 mg daily"}, source="fhir")
    if kind == "stop":                                   # the drug is new: the auto-apply starts it
        _say(m, f"Start {drug} {a}.", {"action": "ADD", "subject": MED, "value": f"{drug} {a}"})
    else:
        _say(m, f"seed {drug}", {"action": "ADD", "subject": MED, "value": f"{drug} {a}"}, source="fhir")
    seed = next(s for s in m.active() if s.value.startswith(drug))
    if kind == "stop":
        pass
    elif kind == "readd":
        _say(m, f"Stop {drug}.", {"action": "RETRACT", "subject": MED, "value": f"{drug} {a}", "target_id": seed.id})
    else:
        _say(m, f"Increase {drug} to {b}.", {"action": "SUPERSEDE", "subject": MED, "value": f"{drug} {b}",
                                             "target_id": seed.id})
    auto = next(s for s in m.statements if (s.metadata or {}).get("auto_applied"))
    auto.created_at = time.time() - days * DAY
    t = text.format(d=drug, a=a, b=b, c=c)
    cur = next((s for s in m.active() if s.value.startswith(drug)), None)
    if kind == "edit":
        m.update(auto.id, f"{drug} {c}", source="clinician")
    elif kind == "stop":
        _say(m, t, {"action": "RETRACT", "subject": MED, "value": cur.value, "target_id": cur.id})
    elif kind == "readd":
        _say(m, t, {"action": "ADD", "subject": MED, "value": f"{drug} {a}"}, source="fhir")
    else:
        _say(m, t, {"action": "SUPERSEDE", "subject": MED, "value": f"{drug} {a if kind == 'back' else c}",
                    "target_id": cur.id})
    md = auto.metadata
    return bool(md.get("corrected")), bool(md.get("corrected") or md.get("changed_by"))


def _score(window_days):
    llm_memory._CORRECTION_WINDOW = window_days * DAY
    t = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for _name, truth, days, kind, text in SCENARIOS:
        for d in DRUGS:
            said = _run(*d, days, kind, text)[0]
            t[("t" if said == truth else "f") + ("p" if said else "n")] += 1
    return t


def main():
    rows, vayl, naive = [], {"tp": 0, "fp": 0, "fn": 0, "tn": 0}, {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for name, truth, days, kind, text in SCENARIOS:
        hits = [_run(*d, days, kind, text) for d in DRUGS]
        for said, touched in hits:
            for tally, guess in ((vayl, said), (naive, touched)):
                tally[("t" if guess == truth else "f") + ("p" if guess else "n")] += 1
        rows.append((name, truth, sum(s for s, _ in hits), sum(t for _, t in hits)))
    print(f"{'scenario':40} {'truth':10} {'vayl':>5} {'naive':>6}   (of {len(DRUGS)} drugs flagged as correction)")
    for name, truth, v, n in rows:
        print(f"{name:40} {'correction' if truth else 'change':10} {v:5} {n:6}")
    for label, t in (("vayl", vayl), ("naive: any later touch", naive)):
        p = t["tp"] / max(1, t["tp"] + t["fp"])
        r = t["tp"] / max(1, t["tp"] + t["fn"])
        print(f"{label:24} precision {p:.2f}  recall {r:.2f}  false corrections {t['fp']}  missed {t['fn']}")
    print("\nwindow sweep (days): false corrections / missed corrections")
    for w in (0.5, 1, 3, 7, 14, 30, 60):
        t = _score(w)
        print(f"  {w:>4}: {t['fp']:2} / {t['fn']:2}")


if __name__ == "__main__":
    main()
