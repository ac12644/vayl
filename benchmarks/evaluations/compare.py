"""Compare episode result files: rates, error counts, asks per message, and an episode-clustered bootstrap
interval for the difference in perfect runs (trials of one episode are not independent).

    python -m benchmarks.evaluations.compare A.json B.json [C.json ...]     # each vs the first
"""
import json
import os
import random
import re
import sys

HERE = os.path.dirname(__file__)
_EPISODES = {}
for name in ("high_stakes_episodes.json", "high_stakes_episodes_holdout.json"):
    for e in json.load(open(os.path.join(HERE, name))):
        _EPISODES[e["id"]] = e


def _others(eid):
    return {n for s in _EPISODES.get(eid, {}).get("steps", []) for n in s.get("other_people", [])}


def _word(n, hay):
    return re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", hay.lower().replace("_", " ")) is not None


def summary(path):
    rows = json.load(open(path))["rows"]
    msgs = sum(len(r["steps"]) for r in rows)
    other = sum(sum(any(_word(n, s) for n in _others(r["id"])) for s, _v in r["end"].get("free_rows", []))
                for r in rows)
    return {"runs": len(rows), "perfect": sum(r["perfect"] for r in rows),
            "lenient": sum(r.get("perfect_lenient", r["perfect"]) for r in rows),
            "severe": sum(len(r["end"].get("severe", [])) for r in rows),
            "lost": sum(len(r["end"]["lost"]) for r in rows), "stale": sum(len(r["end"]["stale"]) for r in rows),
            "forbidden": sum(len(r["end"].get("forbidden_new", r["end"]["forbidden"])) for r in rows),
            # other people's facts kept under their own names, outside the owner's slots: the asked-for
            # behaviour, counted for context and not an error (none landed in an owner's slot on 2026-11-06)
            "others_kept": other, "errors": sum(r["errors"] for r in rows),
            "asks": sum(r["asked"] for r in rows), "asks_per_msg": round(sum(r["asked"] for r in rows) / msgs, 2),
            "_rows": rows}


def boot(a, b, key="perfect", n=20000, seed=7):
    """95% interval for rate(b) - rate(a), resampling episodes, on the episodes both files ran."""
    def by_ep(rows):
        d = {}
        for r in rows:
            d.setdefault(r["id"], []).append(r.get(key, r["perfect"]))
        return d
    A, B = by_ep(a), by_ep(b)
    eps = sorted(set(A) & set(B))
    rng, diffs = random.Random(seed), []
    for _ in range(n):
        pick = [rng.choice(eps) for _ in eps]
        ra = sum(sum(A[e]) for e in pick) / sum(len(A[e]) for e in pick)
        rb = sum(sum(B[e]) for e in pick) / sum(len(B[e]) for e in pick)
        diffs.append(rb - ra)
    diffs.sort()
    return round(100 * diffs[int(0.025 * n)], 1), round(100 * diffs[int(0.975 * n)], 1)


def main():
    paths = sys.argv[1:]
    sums = [summary(p) for p in paths]
    cols = ["runs", "perfect", "lenient", "severe", "lost", "stale", "forbidden", "others_kept", "errors",
            "asks", "asks_per_msg"]
    print("file".ljust(48), *[c[:9].rjust(9) for c in cols], "Δperfect 95% (episode bootstrap)")
    for p, s in zip(paths, sums):
        ci = "" if s is sums[0] else str(boot(sums[0]["_rows"], s["_rows"]))
        print(os.path.basename(p)[:48].ljust(48), *[str(s[c]).rjust(9) for c in cols], ci)


if __name__ == "__main__":
    main()
