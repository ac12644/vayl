"""Hard case histories: several long, messy messages about one patient, client or customer, with traps
hidden inside them. An agent that never saw Vayl's code wrote them. After each message a simulated
reviewer decides everything waiting for approval, approving what matches the author's answer and
rejecting the rest, so a config is judged by the record it ends up with, and by what it asked of a person.

Configs: "off" never holds anything, "shipped" is the preset as it is, "review" holds every change.

    LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-haiku-5-5 \
        python -m benchmarks.evaluations.high_stakes_episodes --config shipped --domain clinical
"""
import argparse
import json
import os
import re
import time

from benchmarks.evaluations.high_stakes import _seed, correct_ops, schema_for
from vayl.memory import llm_memory
from vayl.memory.llm_memory import ConstraintViolation
from vayl.memory.reconcile import Status

HERE = os.path.dirname(__file__)
_EXTRACT = llm_memory.llm_extract_classify
_VERDICT = llm_memory.llm_verdict


def _word(needle, hay):
    """`needle` in `hay` as whole words, so "Ng" does not match "living"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle.strip().lower())}(?![a-z0-9])", hay.lower()) is not None


def _hits(value, match):
    """A match starts on a word boundary but may run on: "webhook" matches "webhooks", "3 year" "3 years"."""
    return any(alt.strip() and re.search(rf"(?<![a-z0-9]){re.escape(alt.strip().lower())}", value.lower())
               for alt in match.split("|"))


def _holds(pairs, slot, match):
    return any(s == slot and _hits(v, match) for s, v in pairs)


class Truth:
    """What should be current, as (slot, match) pairs, following the author's changes step by step."""

    def __init__(self, record):
        self.items = [(e["slot"], e["value"]) for e in record]
        self.gone = []

    def apply(self, changes):
        for c in changes:
            if c["op"] == "remove":
                out = [i for i in self.items if i[0] == c["slot"] and _hits(i[1], c["match"])]
                self.items = [i for i in self.items if i not in out]
                self.gone += out
        for c in changes:
            if c["op"] == "add":
                self.items.append((c["slot"], c["match"]))

    def has(self, slot, value):
        return any(s == slot and _hits(value, m) for s, m in self.items)


def _review(m, truth, stats, q=0.0, rng=None):
    """Approve a proposal only if the record the author expects agrees with it; a group is all or nothing.
    With `q` > 0 the reviewer is fallible: a wrong group is approved anyway with probability q, the
    rate at which people in studies let a wrong AI suggestion through."""
    by_id = {s.id: s for s in m.statements}

    def right(p):
        kind, target = (p.metadata or {}).get("pending"), by_id.get(p.supersedes)
        if target is not None and truth.has(target.subject, target.value):
            return False                             # it would retire something that is still true
        return kind == "RETRACT" or truth.has(p.subject, p.value)

    while m.pending():
        group = m.group(m.pending()[0].id)
        stats["asked"] += 1
        ok = all(right(p) for p in group)
        if not ok and q and rng.random() < q:
            stats["wrong_approved"] = stats.get("wrong_approved", 0) + 1
            ok = True
        if ok:
            try:
                if m.confirm(group[0].id, source="reviewer") is not None:
                    stats["approved"] += 1
                    continue
            except ConstraintViolation:
                pass
            if not any(p.id == group[0].id for p in m.pending()):   # went stale inside confirm
                continue
        m.reject(group[0].id, source="reviewer")
        stats["rejected"] += 1


_SENSITIVE = re.compile(r"kyc|risk_tol|allerg|code_status|dnr|diagnos|medication|holding|_plan$|^plan_|"
                        r"account_status|entitlement|account_type")


def _state(m, truth, step_forbidden, slots, others=(), seen=None, gated=()):
    current = [(s.subject, s.value) for s in m.active()]
    free = [c for c in current if c[0] not in slots]
    lost = [(s, v) for s, v in truth.items if not _holds(current, s, v)]
    # a lost item some current row once held, before a rewording: true, only said differently
    reworded = [(s, v) for s, v in lost if seen and any(
        x.subject == s and any(_hits(old, v) for old in seen.get(x.id, ())) for x in m.active())]
    # a lost item that was on the record and is now retired: removed by mistake, not merely missed
    removed = [(s, v) for s, v in lost if (s, v) not in reworded and any(
        x.subject == s and _hits(x.value, v) for x in m.statements if x.status is not Status.ACTIVE)]
    forbidden = [f["match"] for f in step_forbidden
                 if any(c[0] == f["slot"] and _hits(c[1], f["match"]) and not truth.has(*c) for c in current)]
    return {
        "free": len(free),
        "free_rows": free,
        "free_sensitive": [c for c in free if _SENSITIVE.search(c[0])],
        "other_person": [c for c in free if any(_word(n, c[0].replace("_", " ")) for n in others)],
        # a trap at one step may become true later ("start apixaban next week"), so ask the truth as of now
        "forbidden": forbidden,
        "lost": [v for s, v in lost],
        "reworded": [v for s, v in reworded],
        # severe: a trap written into a declared slot, or a true item on a gated slot removed by mistake
        "severe": forbidden + [v for s, v in removed if s in gated],
        "stale": [c[1] for c in current if any(c[0] == s and _hits(c[1], v) for s, v in truth.gone)
                  and not truth.has(*c)],
        "current": [c for c in current if c[0] in slots],
    }


def _plain(changes, m):
    """The changes as a perfect extractor would write them: every spelling kept, each removal naming a real entry."""
    out = []
    for c in changes:
        if c["op"] == "add":
            out.append({**c, "match": c["match"].replace("|", " / ")})
            continue
        found = next((alt.strip() for alt in c["match"].split("|") for s in m.active()
                      if s.subject == c["slot"] and alt.strip().lower() in s.value.lower()), None)
        if found is None:
            print(f"  data check: nothing current to remove for {c['slot']} {c['match']!r}")
        else:
            out.append({**c, "match": found})
    return out


def run(episode, config, extractor="model", q=0.0, replay=None, seed=0, pad=0):
    """`replay`: the logged steps of an earlier run of this episode; its extractor output is fed back
    instead of calling a model, so reviewer variants can be compared on identical extractions."""
    import random
    rng = random.Random(f"{episode['id']}-{seed}")   # per episode: seeds 0-2 alone reused the same few draws
    llm_memory.SLOT_SCHEMA = schema_for(episode["domain"], config)
    slots = {s.name for s in llm_memory.SLOT_SCHEMA.slots}
    # severity follows the shipped preset whatever the config, so "off" is judged by the same yardstick
    gated = {s.name for s in schema_for(episode["domain"], "shipped").slots if s.confirm or s.confirm_mode == "removal"}
    m, truth = _seed(episode["record"]), Truth(episode["record"])
    stats = {"asked": 0, "approved": 0, "rejected": 0}
    forbidden, steps, errors = [], [], 0
    seen = {}                                        # statement id -> every value it has held
    for step in episode["steps"]:
        t0 = time.perf_counter()
        before = {(s.subject, s.value) for s in m.active()}
        ops_log, verdict_log = [], []
        if extractor == "correct":
            ops = correct_ops({"changes": _plain(step["changes"], m)}, m)
            llm_memory.llm_extract_classify = lambda t, c, *a, o=ops, **k: o
            llm_memory.llm_verdict = lambda *a, **k: []
        elif replay is not None:
            logged_ops = [dict(o) for o in replay[len(steps)].get("ops", [])]
            llm_memory.llm_extract_classify = lambda t, c, *a, o=logged_ops, **k: [dict(x) for x in o]
            logged_v = [dict(o) for o in replay[len(steps)].get("verdict", [])]
            llm_memory.llm_verdict = lambda *a, o=logged_v, **k: [dict(x) for x in o]
        else:
            def verdict(*a, **k):
                out = _VERDICT(*a, **k)
                verdict_log.extend(dict(o) for o in out)
                return out
            llm_memory.llm_verdict = verdict
            def logged(t, c, *a, **k):
                out = _EXTRACT(t, c, *a, **k)
                ops_log.extend({k2: o.get(k2) for k2 in ("action", "subject", "value", "time_ref", "kind",
                                                         "target_id", "confidence", "reason")} for o in out)
                return out
            llm_memory.llm_extract_classify = logged
        llm_memory._LAST_REPLY.update(text="", stop="")
        text = step["message"]
        prior = [s["message"] for s in episode["steps"][:episode["steps"].index(step)]]
        if pad and prior:
            # R18: earlier notes copied forward above today's, as charts do, nested until `pad` characters
            hist = ""
            while len(hist) < pad:
                hist += "\n\n".join(prior) + "\n\n"
            text = f"Copied forward from earlier notes:\n{hist}Today's note:\n{text}"
        try:
            actions = m.add(text, source=step.get("source", ""), owner=episode.get("owner", ""))
        except Exception as e:  # a failed model call is its own outcome, not a crash of the run
            errors += 1
            actions = []
            steps.append({"error": f"{type(e).__name__}: {e}"[:160]})
        for s in m.statements:
            seen.setdefault(s.id, set()).add(s.value)
        truth.apply(step["changes"])
        forbidden += step.get("forbidden", [])
        unreviewed = _state(m, truth, forbidden, slots)
        unapproved = [c["match"] for c in step["changes"] if c.get("needs_human") and c["op"] == "add"
                      and _holds({(s.subject, s.value) for s in m.active()} - before, c["slot"], c["match"])]
        unapproved += [c["match"] for c in step["changes"] if c.get("needs_human") and c["op"] == "remove"
                       and _holds(before, c["slot"], c["match"])
                       and not _holds({(s.subject, s.value) for s in m.active()}, c["slot"], c["match"])]
        _review(m, truth, stats, q, rng)
        steps.append({"actions": [f"{a.value} {s}={v}" for a, s, v in actions], "ops": ops_log, "verdict": verdict_log,
                      "raw": llm_memory._LAST_REPLY["text"], "stop": llm_memory._LAST_REPLY["stop"],
                      "ms": round((time.perf_counter() - t0) * 1000), "unreviewed": unreviewed,
                      "unapproved": unapproved, "needs_human": sum(bool(c.get("needs_human")) for c in step["changes"])})
    others = {n for step in episode["steps"] for n in step.get("other_people", [])}
    end = _state(m, truth, forbidden, slots, others, seen, gated)
    perfect = not (end["forbidden"] or end["lost"] or end["stale"]) and not errors
    # lenient: a true item that is still current, only reworded, is not counted as lost
    lenient = not (end["forbidden"] or set(end["lost"]) - set(end["reworded"]) or end["stale"]) and not errors
    return {"id": episode["id"], "domain": episode["domain"], "perfect": perfect,
            "perfect_lenient": lenient, "errors": errors,
            "end": end, "steps": steps, **stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default=os.path.join(HERE, "high_stakes_episodes.json"))
    ap.add_argument("--config", default="shipped", choices=("off", "shipped", "review"))
    ap.add_argument("--extractor", default="model", choices=("model", "correct"))
    ap.add_argument("--domain", default="")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--tag", default="", help="suffix for the results file, so phases don't overwrite each other")
    ap.add_argument("--q", type=float, default=0.0, help="chance the reviewer approves a wrong proposal")
    ap.add_argument("--pad", type=int, default=0, help="copy earlier notes forward to this many characters")
    ap.add_argument("--replay", default="", help="a results file whose logged extractions are fed back")
    args = ap.parse_args()
    episodes = [e for e in json.load(open(args.episodes)) if not args.domain or e["domain"] == args.domain]
    model = os.environ.get("ANTHROPIC_MODEL", "") if args.extractor == "model" else args.extractor
    suffix = (f"-{args.domain}" if args.domain else "") + (f"-{args.tag}" if args.tag else "")
    stem = os.path.splitext(os.path.basename(args.episodes))[0].replace("high_stakes_", "")
    out = os.path.join(HERE, "..", "results", f"{stem}-{model}-{args.config}{suffix}.json")
    rows = []
    logged = {}
    if args.replay:
        for r in json.load(open(args.replay))["rows"]:
            logged.setdefault(r["id"], []).append(r["steps"])
        model = "replay"
    for e in episodes:
        runs = logged.get(e["id"], []) if args.replay else [None] * args.trials
        for i, rp in enumerate(runs):
            if rp is not None and any("error" in st for st in rp):
                continue                      # a failed call has no extraction to replay
            rows.append(run(e, args.config, args.extractor, args.q, rp, seed=i, pad=args.pad))
            r = rows[-1]
            print(f"{r['id']:8} perfect={r['perfect']!s:5} lenient={r['perfect_lenient']!s:5} "
                  f"severe={len(r['end']['severe'])} asked={r['asked']} rejected={r['rejected']} "
                  f"forbidden={len(r['end']['forbidden'])} lost={len(r['end']['lost'])} "
                  f"stale={len(r['end']['stale'])} errors={r['errors']}", flush=True)
            with open(out, "w") as f:   # saved as it goes
                json.dump({"model": model, "config": args.config, "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main()
