"""
Mutation test: do the engine checks notice when the code is wrong?

Each mutant copies the package, plants one bug (weight limits ignored, a box allowed to float, drop-off order
broken, the checker made lenient, ...) and runs verify_engine.py against the copy. Every mutant must be caught.

    python tests/mutation_test.py            # about 25 minutes (each copy compiles the engine again)
    python tests/mutation_test.py stop       # only the mutants whose name contains "stop"
    python tests/mutation_test.py --check    # only check that every mutant still applies to the code
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUTANTS = [
    # the engine
    ("pressure on a floor not checked", "engine.py",
     "        if bw[i] > wleft or bp[i] > cap:", "        if bw[i] > wleft:"),
    ("payload not checked", "engine.py",
     "        if bw[i] > wleft or bp[i] > cap:", "        if bp[i] > cap:"),
    ("box counts not checked", "engine.py",
     "            if rem[t1] < B[i, 6]:\n", "            if rem[t1] < 0:\n"),
    ("space above a block covers more than its top", "engine.py",
     "        out[m, 0], out[m, 1], out[m, 2] = max(x0, s0), max(ty0, s1), z0 + bz",
     "        out[m, 0], out[m, 1], out[m, 2] = s0, s1, z0 + bz"),
    ("joined spaces span more than the two spaces", "engine.py",
     "                y0, y1 = max(sp[i, 1], sp[j, 1]), min(sp[i, 4], sp[j, 4])",
     "                y0, y1 = min(sp[i, 1], sp[j, 1]), max(sp[i, 4], sp[j, 4])"),
    ("stacks of one kind ignore the weight limit", "engine.py",
     "            zmax = 1 + int(np.floor(bear / wb + 1e-9))", "            zmax = q"),
    ("a block on a block ignores the lower one's limit", "engine.py",
     "            ok &= Al[lo] >= Pr[up]\n", "            ok &= Al[lo] >= 0\n"),
    ("fragile boxes may carry weightless ones", "engine.py",
     "                    allow = -1.0", "                    allow = 0.0"),
    ("this side up ignored", "engine.py",
     "        if upright[i]:\n            a, b = [int(dims[j])", "        if True:\n            a, b = [int(dims[j])"),
    ("loading order puts boxes in front first", "engine.py",
     "    behind = (b[None, :, 3] <= b[:, None, 0]) & oy & oz          # j is behind i in its lane",
     "    behind = (b[None, :, 0] >= b[:, None, 3]) & oy & oz          # j is behind i in its lane"),
    ("loading order ignores what a box rests on", "engine.py",
     "    need = rest | behind\n", "    need = behind\n"),
    # drop-offs, by hand, balance, fleet
    ("later stops not closed off behind", "planner.py",
     "        sp, cap = _cut(sp, cap, lane, mind)        # this also", "        pass  # this also"),
    ("stops packed in the wrong order", "planner.py",
     "    stops = sorted({int(s) for s in cargo.stop[cargo.count > 0]}, reverse=True)\n    vol_t",
     "    stops = sorted({int(s) for s in cargo.stop[cargo.count > 0]})\n    vol_t"),
    ("later stops closed off only where their boxes are", "planner.py",
     "        lane[:, 0] = 0                             # nothing for an earlier stop behind these boxes (nor in them:",
     "        lane[:, 0] = lane[:, 0]"),
    ("by hand ignores pressure", "planner.py",
     " or press * areas[k] > br[col[k][0]] + 1e-9:", ":"),
    ("by hand stacks on boxes too small", "planner.py",
     "                            if l2 <= tl and w2 <= tw and z + h2 <= H", "                            if z + h2 <= H"),
    ("moving a load may push it out of the door", "planner.py",
     "    shift = int(np.clip(round(plan.vehicle.L / 2 - cog), 0, room))",
     "    shift = int(max(round(plan.vehicle.L / 2 - cog), 0))"),
    ("fleet forgets what was loaded", "planner.py",
     "        remaining = remaining - pl.loaded\n", "        remaining = remaining\n"),
    # the checker
    ("checker: half support is enough", "check.py",
     "def problems(boxes, cargo: Cargo, veh: Vehicle, support=1.0):",
     "def problems(boxes, cargo: Cargo, veh: Vehicle, support=0.5):"),
    ("checker: pressure on one spot not checked", "check.py",
     "    over = (on > bear + 1e-6) | (press * foot > bear * (1 + 1e-9) + 1e-6)", "    over = (on > bear + 1e-6)"),
    ("checker: boxes in front of others ignored", "check.py",
     "    if (later & (in_front | on_top)).any():", "    if (later & on_top).any():"),
    ("checker: overlaps not checked", "check.py", "    if ov.max() > 0:", "    if ov.max() > 1e18:"),
    ("checker: payload not checked", "check.py", "    if w.sum() > veh.payload + 1e-6:", "    if False:"),
    # files and pages
    ("this side up read the wrong way round", "demo.py",
     "        up.append([False, False, True] if side_up else [True, True, True])",
     "        up.append([True, True, True] if side_up else [False, False, True])"),
    ("names can break out of the 3D page", "viewer.py",
     '    js = json.dumps(data, separators=(",", ":")).replace("</", "<\\\\/")',
     '    js = json.dumps(data, separators=(",", ":"))'),
    ("steps say the wrong box underneath", "report.py",
     '", ".join(str(j + 1) for j in np.flatnonzero(rest[i]))', '", ".join(str(j) for j in np.flatnonzero(rest[i]))'),
]


def main():
    args = sys.argv[1:]
    only_check = "--check" in args
    only = [a for a in args if not a.startswith("--")]
    todo = [m for m in MUTANTS if not only or any(w in m[0] for w in only)]
    caught = 0
    for name, fname, old, new in todo:
        src = open(os.path.join(ROOT, "loadplan", fname), encoding="utf-8").read()
        if src.count(old) != 1:
            print(f"  BROKEN  {name}: the text to mutate is found {src.count(old)} times")
            continue
        if only_check:
            caught += 1
            continue
        tmp = tempfile.mkdtemp(prefix="mutant_")
        try:
            shutil.copytree(os.path.join(ROOT, "loadplan"), os.path.join(tmp, "loadplan"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            if os.path.isdir(os.path.join(ROOT, "data_cache")):
                os.symlink(os.path.join(ROOT, "data_cache"), os.path.join(tmp, "data_cache"))
            open(os.path.join(tmp, "loadplan", fname), "w", encoding="utf-8").write(src.replace(old, new))
            try:
                res = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tests", "verify_engine.py")],
                                     env=dict(os.environ, LP_ROOT=tmp), capture_output=True, text=True, timeout=1500)
            except subprocess.TimeoutExpired:
                print(f"  MISSED  {name:55s}  (checks did not finish)", flush=True)
                continue
            fails = [ln.strip()[6:] for ln in res.stdout.splitlines() if ln.strip().startswith("FAIL")]
            hit = res.returncode != 0
            caught += hit
            why = fails[0] if fails else (res.stderr.strip().splitlines() or ["crash"])[-1]
            print(f"  {'CAUGHT' if hit else 'MISSED'}  {name:55s}  {why[:80] if hit else ''}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    word = "apply" if only_check else "caught"
    print(f"\n{caught} of {len(todo)} mutants {word}")
    sys.exit(0 if caught == len(todo) else 1)


if __name__ == "__main__":
    main()
