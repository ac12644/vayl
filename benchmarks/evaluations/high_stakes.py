"""Does Vayl keep the record right when a wrong fact causes harm? Scenarios for clinical, finance and
support were written by an agent that never saw Vayl's code, and that agent decided which changes need a
person. One message goes through the real `LLMMemory.add()`, read by a real model; the record is scored
before anyone approves anything:

    WRONG       the record is wrong: an entry lost that should stay, or a current value nobody stated
    UNAPPROVED  a change the scenario says needs a person became current without one
    PARTIAL     safe, but only some expected changes were applied or queued
    MISSED      safe, but no expected change was applied or queued, so the stale fact stands
    GOOD        every expected change was applied or queued; or nothing should change and nothing did

Configs: "shipped" uses the preset as it is, "auto" and "review" put every slot of the preset on that gate.

    OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama OPENAI_MODEL=qwen3 \
        python -m benchmarks.evaluations.high_stakes --config shipped
"""
import argparse
import collections
import json
import os
import time

os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("VAYL_TRUSTED_SOURCES", "seed")

from vayl.memory import llm_memory  # noqa: E402
from vayl.memory.llm_memory import LLMMemory  # noqa: E402
from vayl.memory.schema import from_dict  # noqa: E402

HERE = os.path.dirname(__file__)
OUTCOMES = ("WRONG", "UNAPPROVED", "PARTIAL", "MISSED", "GOOD", "ERROR")


def schema_for(domain, config):
    import importlib.resources as res
    raw = json.loads(res.files("vayl.presets").joinpath(domain + ".json").read_text(encoding="utf-8"))
    if config == "off":        # nothing is ever held, rules over several facts included
        raw.pop("constraints", None)
    if config != "shipped":
        for slot in raw["slots"]:
            slot["confirm"] = False if config == "off" else config
    return from_dict(raw)


def _seed(record):
    m = LLMMemory()
    for e in record:
        m._apply({"action": "ADD", "subject": e["slot"], "value": e["value"], "confidence": 0.95,
                  "kind": "state", "time_ref": "present"}, f"(seed) {e['value']}", source="seed")
    return m


def score(scenario, m, slots):
    """Pure scoring of one trial, so the rules can be tested without a model."""
    record, changes = scenario["record"], scenario["changes"]
    current = [(s.subject, s.value.lower()) for s in m.active()]
    by_id = {s.id: s for s in m.statements}
    queued_add, queued_remove = [], []
    for p in m.pending():
        target = by_id.get(p.supersedes)
        if target is not None:
            queued_remove.append((target.subject, target.value.lower()))
        if (p.metadata or {}).get("pending") != "RETRACT":
            queued_add.append((p.subject, p.value.lower()))
    has = lambda pairs, c: any(s == c["slot"] and c["match"].lower() in v for s, v in pairs)   # noqa: E731
    removes = [c for c in changes if c["op"] == "remove"]
    adds = [c for c in changes if c["op"] == "add"]

    lost = [e for e in record if (e["slot"], e["value"].lower()) not in current and not any(
        c["slot"] == e["slot"] and c["match"].lower() in e["value"].lower() for c in removes)]
    start = {(e["slot"], e["value"].lower()) for e in record}
    invented = [(s, v) for s, v in current if s in slots and (s, v) not in start and not any(
        c["slot"] == s and c["match"].lower() in v for c in adds)]
    status, unapproved = [], []
    for c in changes:
        if c["op"] == "add":
            done, waiting = has([p for p in current if p not in start], c), has(queued_add, c)
        else:
            done, waiting = not has(current, c), has(queued_remove, c)
        status.append("applied" if done else "queued" if waiting else "missed")
        if done and c.get("needs_human"):
            unapproved.append(c["match"])
    if lost or invented:
        outcome = "WRONG"
    elif unapproved:
        outcome = "UNAPPROVED"
    elif not changes or "missed" not in status:
        outcome = "GOOD"
    else:
        outcome = "PARTIAL" if any(x != "missed" for x in status) else "MISSED"
    return outcome, {"status": status, "lost": [e["value"] for e in lost], "invented": invented,
                     "unapproved": unapproved, "current": current, "queued_add": queued_add,
                     "queued_remove": queued_remove}


def correct_ops(scenario, m):
    """What an extractor that reads the message perfectly would return, to test the gates by themselves."""
    base = {"confidence": 0.9, "kind": "state", "time_ref": "present", "scope": "global"}
    target = lambda c: next(s for s in m.active() if s.subject == c["slot"] and c["match"].lower() in s.value.lower())  # noqa: E731
    removes = [c for c in scenario["changes"] if c["op"] == "remove"]
    adds = [c for c in scenario["changes"] if c["op"] == "add"]
    ops = []
    for r in removes:
        a = next((x for x in adds if x["slot"] == r["slot"]), None)
        if a:
            adds.remove(a)
            ops.append({**base, "action": "SUPERSEDE", "subject": r["slot"], "value": a["match"], "target_id": target(r).id})
        else:
            ops.append({**base, "action": "RETRACT", "subject": r["slot"], "value": target(r).value, "target_id": target(r).id})
    return ops + [{**base, "action": "ADD", "subject": a["slot"], "value": a["match"]} for a in adds]


def trial(scenario, config, extractor="model"):
    llm_memory.SLOT_SCHEMA = schema_for(scenario["domain"], config)
    m = _seed(scenario["record"])
    if extractor == "correct":
        ops = correct_ops(scenario, m)
        llm_memory.llm_extract_classify = lambda t, c: ops
    elif extractor == "wrong":
        ops = [{"confidence": 0.9, "kind": "state", "time_ref": "present", "scope": "global", **o}
               for o in scenario["wrong_ops"]]
        for o in ops:     # the wrong write names a real record entry when it targets one
            if o["action"] != "ADD":
                match = o.pop("match").lower()
                o["target_id"] = next(s.id for s in m.active() if s.subject == o["subject"] and match in s.value.lower())
        llm_memory.llm_extract_classify = lambda t, c: ops
    t0 = time.perf_counter()
    try:
        actions = m.add(scenario["message"])
    except Exception as e:  # a failed model call is its own outcome, not a crash of the run
        return {"id": scenario["id"], "outcome": "ERROR", "error": f"{type(e).__name__}: {e}"[:200]}
    outcome, detail = score(scenario, m, {s.name for s in llm_memory.SLOT_SCHEMA.slots})
    return {"id": scenario["id"], "domain": scenario["domain"], "outcome": outcome,
            "ms": round((time.perf_counter() - t0) * 1000),
            "actions": [f"{a.value} {s}={v}" for a, s, v in actions], **detail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default=os.path.join(HERE, "high_stakes_scenarios.json"))
    ap.add_argument("--config", default="shipped", choices=("shipped", "auto", "review"))
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--domain", default="")
    ap.add_argument("--extractor", default="model", choices=("model", "correct", "wrong"),
                    help="model: a real model call; correct: perfect operations; wrong: scripted wrong operations")
    args = ap.parse_args()
    scenarios = [s for s in json.load(open(args.scenarios)) if not args.domain or s["domain"] == args.domain]
    scenarios = scenarios[:args.limit] if args.limit else scenarios
    if args.extractor == "wrong":
        scenarios = [s for s in scenarios if s.get("wrong_ops")]
    model = os.environ.get(f"{os.environ['LLM_PROVIDER'].upper()}_MODEL", "") if args.extractor == "model" else args.extractor
    stem = os.path.splitext(os.path.basename(args.scenarios))[0].replace("high_stakes_scenarios", "high_stakes")
    out = os.path.join(HERE, "..", "results", f"{stem}-{model.replace(':', '_')}-{args.config}.json")
    rows = []
    for s in scenarios:
        for _ in range(args.trials):
            rows.append(trial(s, args.config, args.extractor))
            print(f"{rows[-1]['id']:10} {rows[-1]['outcome']:11} {rows[-1].get('ms', 0):7}ms", flush=True)
            with open(out, "w") as f:   # saved as it goes: a local model run can take hours
                json.dump({"model": model, "config": args.config, "rows": rows}, f, indent=1)
    print(f"\nmodel={model} config={args.config} trials={len(rows)}")
    for d in sorted({s["domain"] for s in scenarios}):
        c = collections.Counter(r["outcome"] for r in rows if r.get("domain") == d)
        print(f"{d:10}", "  ".join(f"{k} {c[k]}" for k in OUTCOMES))
    print("errors", sum(r["outcome"] == "ERROR" for r in rows))


if __name__ == "__main__":
    main()
