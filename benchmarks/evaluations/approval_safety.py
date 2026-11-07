"""What happens at APPROVAL time: replays the recorded model outputs (med_change_replay.SHAPES) in
review mode, then has a reviewer act on the queue, and counts the records left with two
anticoagulants current BECAUSE OF AN APPROVAL (a new anticoagulant joined an over-limit class).

Two reviewer behaviours, both common in practice:
  half      approves only the start of the switch (the risky half) and leaves the rest for later
  drift     before anyone approves, a trusted feed adds rivaroxaban; then everything is approved

Run under two approval rules:
  per-item  each proposal applies on its own, with no re-check (Vayl before linked groups)
  linked    proposals from one message apply together, and declared constraints are re-checked
            against the record as it is at approval (refused unless overridden)

    VAYL_CONFIRM_MODE=review python -m benchmarks.evaluations.approval_safety
"""
import os

os.environ.setdefault("VAYL_SLOT_SCHEMA", "preset:clinical")
os.environ.setdefault("VAYL_CONFIRM_MODE", "review")
import benchmarks.evaluations.med_change_replay as replay  # noqa: E402
import benchmarks.evaluations.med_change_safety as live  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402

ANTI = {"warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "enoxaparin", "heparin"}


def _anti(m):
    return live._meds(m.active()) & ANTI


def _two_anticoagulants(m):
    return len(_anti(m)) > 1


def _approve(m, pid, linked):
    if linked:
        try:
            m.confirm(pid, source="reviewer")
        except llm_memory.ConstraintViolation:
            pass                                       # refused: the reviewer has to look again
        return
    st = next((s for s in m.pending() if s.id == pid), None)
    if st is not None and not m._stale(st):
        m._confirm_one(st, "reviewer", False)          # the old rule: this item alone, no re-check


def _trial(case, facts, behaviour, linked):
    base = {"scope": "global", "confidence": 0.9, "kind": "state", "time_ref": "present"}
    llm_memory.llm_extract_classify = lambda t, c: [{**base, **f} for f in facts]
    llm_memory.llm_verdict = lambda *a, **k: []   # recordings hold the first pass only
    llm_memory._ESCALATE = ""                       # and no escalation check
    m = live._seed()
    m.add(case[1])
    if _two_anticoagulants(m):
        return "unsafe-at-write"
    if not m.pending():
        return "nothing-queued"
    if behaviour == "drift":
        llm_memory.llm_extract_classify = lambda t, c: [{**base, "action": "ADD", "subject": "active_medication",
                                                          "value": "rivaroxaban 20 mg daily"}]
        m.add("rivaroxaban 20 mg daily", source="fhir")
        pre = _anti(m)
        for p in list(m.pending()):
            _approve(m, p.id, linked)
    else:
        pre = _anti(m)
        start = next((p for p in m.pending() if p.metadata.get("pending") != "RETRACT"), m.pending()[0])
        _approve(m, start.id, linked)
    return "UNSAFE" if _two_anticoagulants(m) and _anti(m) - pre else "safe"


def main():
    cases = {c[0]: c for c in live.CASES + live.HOLDOUT + live.RISK}
    switches = {cid: s for cid, s in replay.SHAPES.items() if "switch" in cid or cid.startswith("h_replaced")}
    print(f"{'behaviour':8} {'rule':9} {'trials':>6} {'UNSAFE':>7} {'safe':>6}")
    for behaviour in ("half", "drift"):
        for linked in (False, True):
            tally = {}
            for cid, shapes in switches.items():
                for n, facts in shapes:
                    out = _trial(cases[cid], facts, behaviour, linked)
                    tally[out] = tally.get(out, 0) + n
            total = sum(v for k, v in tally.items() if k in ("UNSAFE", "safe"))
            print(f"{behaviour:8} {'linked' if linked else 'per-item':9} {total:6} {tally.get('UNSAFE', 0):7} "
                  f"{tally.get('safe', 0):6}   {dict(sorted(tally.items()))}")


if __name__ == "__main__":
    main()
