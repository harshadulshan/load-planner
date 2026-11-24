"""
Benchmark on the BR1-BR15 container loading test sets (100 problems each), every box fully supported.

For every problem: the planner (time limit per problem), the same engine without search (largest block first), and
the by-hand plan. Every plan is checked box by box before it counts. Results go to benchmarks/results/ as one JSON
line per problem (a run can be stopped and continued), and the summary to benchmarks/results_br.json.

    python benchmarks/run_br.py --seconds 5 --procs 2          # all sets
    python benchmarks/run_br.py --sets 1 7 --first 10          # a quick look
    python benchmarks/run_br.py --summary                      # summary only
    python benchmarks/run_br.py --redo by_hand                 # run one method again on every problem
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import platform
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from loadplan import brdata, check, planner  # noqa: E402

RUNS = os.path.join(HERE, "results", "br_runs.jsonl")
SUMMARY = os.path.join(HERE, "results_br.json")


def one(args, only=None):
    k, idx, seconds = args
    num, cargo, veh = brdata.load(k)[idx]
    row = {"set": k, "problem": int(num), "seconds_limit": seconds}
    for name, run in (("planner", lambda: planner.plan_load(cargo, veh, seconds)),
                      ("no_search", lambda: planner.plan_load(cargo, veh, seconds, widths=(1,))),
                      ("by_hand", lambda: planner.by_hand(cargo, veh))):
        if only and name != only:
            continue
        t0 = time.time()
        plan = run()
        took = time.time() - t0
        errs = check.problems(plan.boxes, cargo, veh)
        row[name] = {"fill": round(plan.fill * 100, 4), "seconds": round(took, 3), "boxes": int(len(plan.boxes)),
                     "valid": not errs, "problems": errs}
    return row


def done_keys():
    if not os.path.exists(RUNS):
        return set()
    with open(RUNS) as f:
        return {(r["set"], r["problem"]) for r in map(json.loads, f) if r}


def summary():
    rows = [json.loads(line) for line in open(RUNS) if line.strip()]
    pub = json.load(open(os.path.join(HERE, "br_published.json")))
    out = {"sets": {}, "machine": {"python": platform.python_version(), "cpus": os.cpu_count(),
                                   "processor": platform.processor() or platform.machine()}}
    for k in sorted({r["set"] for r in rows}):
        rs = [r for r in rows if r["set"] == k]
        s = {"problems": len(rs)}
        for name in ("planner", "no_search", "by_hand"):
            f = np.array([r[name]["fill"] for r in rs])
            s[name] = {"mean_fill": round(float(f.mean()), 3), "min_fill": round(float(f.min()), 3),
                       "max_fill": round(float(f.max()), 3),
                       "mean_seconds": round(float(np.mean([r[name]["seconds"] for r in rs])), 3),
                       "invalid": int(sum(not r[name]["valid"] for r in rs))}
        s["seconds_limit"] = rs[0]["seconds_limit"]
        out["sets"][f"BR{k}"] = s
    sets = list(out["sets"])
    groups = {"BR1-7": [f"BR{k}" for k in range(1, 8)], "BR8-15": [f"BR{k}" for k in range(8, 16)],
              "BR1-15": [f"BR{k}" for k in range(1, 16)]}
    out["averages"] = {}
    for g, members in groups.items():
        if all(m in sets for m in members):
            out["averages"][g] = {name: round(float(np.mean([out["sets"][m][name]["mean_fill"] for m in members])), 3)
                                  for name in ("planner", "no_search", "by_hand")}
    out["published"] = pub["averages_from_table"]
    out["published_source"] = pub["source"]
    with open(SUMMARY, "w") as f:
        json.dump(out, f, indent=1)
    for k, s in out["sets"].items():
        print(f"{k:5s} {s['planner']['mean_fill']:6.2f}%  no search {s['no_search']['mean_fill']:6.2f}%  "
              f"by hand {s['by_hand']['mean_fill']:6.2f}%  ({s['planner']['mean_seconds']:.1f} s, "
              f"invalid {s['planner']['invalid'] + s['no_search']['invalid'] + s['by_hand']['invalid']})")
    for g, a in out["averages"].items():
        print(g, a)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", type=int, nargs="*", default=list(range(1, 16)))
    ap.add_argument("--first", type=int, default=100)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--redo", choices=["planner", "no_search", "by_hand"])
    a = ap.parse_args()
    if a.summary:
        summary()
        return
    if a.redo:
        rows = [json.loads(line) for line in open(RUNS) if line.strip()]
        idx = {k: {int(n): i for i, (n, _, _) in enumerate(brdata.load(k))} for k in {r["set"] for r in rows}}
        for n, r in enumerate(rows, 1):
            r[a.redo] = one((r["set"], idx[r["set"]][r["problem"]], r["seconds_limit"]), only=a.redo)[a.redo]
            if n % 250 == 0:
                print(f"{n}/{len(rows)}", flush=True)
        with open(RUNS + ".tmp", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        os.replace(RUNS + ".tmp", RUNS)
        summary()
        return
    os.makedirs(os.path.dirname(RUNS), exist_ok=True)
    have = done_keys()
    todo = []
    for k in a.sets:
        for idx, (num, _, _) in enumerate(brdata.load(k)[:a.first]):
            if (k, int(num)) not in have:
                todo.append((k, idx, a.seconds))
    print(f"{len(todo)} problems to run", flush=True)
    planner.plan_load(*brdata.load(1)[0][1:], 0.2)           # compile once before the workers start
    t0 = time.time()
    method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"      # Windows has no fork
    with mp.get_context(method).Pool(a.procs) as pool, open(RUNS, "a") as f:
        for n, row in enumerate(pool.imap_unordered(one, todo), 1):
            f.write(json.dumps(row) + "\n")
            f.flush()
            if n % 25 == 0 or n == len(todo):
                print(f"{n}/{len(todo)}  {time.time() - t0:.0f} s", flush=True)
    summary()


if __name__ == "__main__":
    main()
