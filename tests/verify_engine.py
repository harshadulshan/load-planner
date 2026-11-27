"""
Engine checks: the box-by-box checker catches every kind of bad plan; plans from the planner are valid on benchmark
loads, on random loads with weights, fragile boxes, payloads and several drop-offs; the loading order can be followed;
the by-hand plan, part-load balancing and fleet planning keep every rule; the demo, the workbook, the 3D page and the
PDF behave.

    python tests/verify_engine.py      # about 3 minutes (the benchmark loads are downloaded on first use)
"""

from __future__ import annotations

import io
import math
import os
import sys

ROOT = os.environ.get("LP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from loadplan import brdata, check, demo, engine, planner, report, viewer, workbook  # noqa: E402
from loadplan.engine import Cargo, Vehicle  # noqa: E402

passed, failed = 0, []


def ok(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {detail}")


def valid(plan):
    return not check.problems(plan.boxes, plan.cargo, plan.vehicle)


def random_load(rng, weights=True, stops=1, T=None):
    T = T or int(rng.integers(3, 12))
    dims = rng.integers(25, 90, size=(T, 3))
    up = rng.random((T, 3)) < 0.6
    up[:, 2] = True
    cnt = rng.integers(4, 45, size=T)
    w = rng.uniform(2, 60, size=T) if weights else None
    bear = (np.where(rng.random(T) < 0.25, 0.0, np.where(rng.random(T) < 0.5, rng.uniform(5, 150, T), np.inf))
            if weights else None)
    st = rng.integers(1, stops + 1, size=T) if stops > 1 else None
    return Cargo(dims, up, cnt, w, bear, st)


# compile the engine before anything is timed (a fresh copy compiles for about 15 s)
planner.plan_load(Cargo([[10, 10, 10], [10, 10, 10]], [[1, 1, 1]] * 2, [3, 3], stop=[1, 2]), Vehicle("w", 50, 20, 20), 0.2)
planner.plan_load(Cargo([[10, 10, 10]], [[1, 1, 1]], [5]), Vehicle("w", 50, 20, 20), 0.2)

# ------------------------------------------------------------------------------------------------ the checker
print("The checker")
V = Vehicle("v", 300, 200, 200, payload=100)
C = Cargo([[100, 100, 50], [10, 10, 10], [100, 100, 50]], [[0, 0, 1], [1, 1, 1], [0, 0, 1]], [3, 5, 2],
          [10, 15, 10], [15, np.inf, 0], [1, 1, 2])
good = np.array([[0, 0, 0, 100, 100, 50, 0], [0, 0, 50, 100, 100, 100, 0]])
ok("a supported stack within limits passes", check.problems(good, C, V) == [], check.problems(good, C, V))


def caught(name, boxes, word, cargo=C, veh=V):
    e = check.problems(np.array(boxes), cargo, veh)
    ok(f"caught: {name}", any(word in x for x in e), e)


caught("two boxes in the same place", [[0, 0, 0, 100, 100, 50, 0], [50, 0, 0, 150, 100, 50, 0]], "overlap")
caught("a box outside the vehicle", [[250, 0, 0, 350, 100, 50, 0]], "sticks out")
caught("a floating box", [[0, 0, 60, 100, 100, 110, 0]], "not fully supported")
caught("a box with only part of it supported", [[0, 0, 0, 100, 100, 50, 0], [30, 0, 50, 130, 100, 100, 0]],
       "not fully supported")
caught("a this-side-up box on its side", [[0, 0, 0, 100, 50, 100, 0]], "may not")
caught("a box with the wrong size", [[0, 0, 0, 100, 90, 50, 0]], "may not")
caught("more boxes than there are", [[0, 0, 0, 100, 100, 50, 2], [100, 0, 0, 200, 100, 50, 2],
                                     [200, 0, 0, 300, 100, 50, 2]], "more boxes")
caught("over the payload", [[0, 0, 0, 100, 100, 50, 0], [100, 0, 0, 200, 100, 50, 0]] +
       [[200 + 10 * i, 0, 0, 210 + 10 * i, 10, 10, 1] for i in range(5)], "payload",
       veh=Vehicle("v", 300, 200, 200, payload=60))
caught("more weight on a box than it can bear", [[0, 0, 0, 100, 100, 50, 0], [0, 0, 50, 100, 100, 100, 0],
                                                  [0, 0, 100, 100, 100, 150, 0]], "bear")
caught("a small heavy box pressing on one spot", [[0, 0, 0, 100, 100, 50, 0], [0, 0, 50, 10, 10, 60, 1]], "bear")
caught("anything on a box that must have nothing on top",
       [[0, 0, 0, 100, 100, 50, 2], [0, 0, 50, 10, 10, 60, 1]], "nothing on top",
       cargo=Cargo(C.dims, C.upright, C.count, [10, 0, 10], C.bear, C.stop))
caught("a later stop between a box and the door", [[0, 0, 0, 100, 100, 50, 0], [100, 0, 0, 200, 100, 50, 2]],
       "later stop")
caught("a later stop on top of an earlier one", [[0, 0, 0, 100, 100, 50, 0], [0, 0, 50, 100, 100, 100, 2]],
       "later stop")
ok("an earlier stop in front of a later one passes",
   check.problems(np.array([[0, 0, 0, 100, 100, 50, 2], [100, 0, 0, 200, 100, 50, 0]]), C, V) == [])
ok("a box beside a later stop's box passes",
   check.problems(np.array([[0, 0, 0, 100, 100, 50, 0], [0, 100, 0, 100, 200, 50, 2]]), C, V) == [])

# ------------------------------------------------------------------------------------------------ benchmark loads
print("Benchmark loads (BR, full support)")
fills = {}
for k, n in [(1, 3), (8, 2), (15, 2)]:
    f, bad, mono = [], 0, True
    for num, cg, veh in brdata.load(k)[:n]:
        p = planner.plan_load(cg, veh, 1.5)
        g = planner.plan_load(cg, veh, 1.5, widths=(1,), mode=0)
        bad += not valid(p) or not valid(g)
        mono &= p.volume >= g.volume
        f.append(p.fill)
    fills[k] = np.mean(f) * 100
    ok(f"BR{k}: every plan valid", bad == 0, bad)
    ok(f"BR{k}: the search never does worse than no search", mono)
ok("BR1 fill above 90%", fills[1] > 90, fills)
ok("BR8 fill above 87%", fills[8] > 87, fills)
ok("BR15 fill above 84%", fills[15] > 84, fills)
num, cg, veh = brdata.load(4)[0]
pk = engine.Packer(cg, veh)
vol, placed, _ = pk.solve(0.8)
bx = engine.expand(placed, pk.bl)
ok("expanded boxes have the volume the search reports", int((bx[:, 3:6] - bx[:, :3]).prod(1).sum()) == vol)
st, _, _ = pk.replay(placed)
ok("the search can be replayed to the same state", st.vol == vol)
blk = engine.make_blocks(cg, veh)
vols = blk.B[:, engine.VOL]
okv = True
for i in range(0, len(blk.B), max(1, len(blk.B) // 300)):
    a, b = blk.parts[i]
    box = cg.dims.prod(axis=1)
    parts = [blk.simple[a]] if blk.kind[i] == 0 else [blk.simple[a], blk.simple[b]]
    okv &= sum(int(box[p[0]]) * int(p[4] * p[5] * p[6]) for p in parts) == vols[i]
ok("every block's volume is the volume of its boxes", okv)
ok("BR data: 100 loads in a 587 x 233 x 220 container", len(brdata.load(1)) == 100 and
   (brdata.load(1)[0][2].L, brdata.load(1)[0][2].W, brdata.load(1)[0][2].H) == (587, 233, 220))

# ------------------------------------------------------------------------------------------------ weights
print("Weights, fragile boxes, payload")
rng = np.random.default_rng(11)
bad = 0
for it in range(14):
    cg = random_load(rng)
    veh = Vehicle("t", 500, 240, 240, payload=float(rng.choice([2500, 6000, 12000])))
    p = planner.plan_load(cg, veh, 0.7)
    e = check.problems(p.boxes, cg, veh)
    bad += bool(e)
ok("random weighted loads: every plan valid", bad == 0, bad)
cg = Cargo([[60, 40, 40], [30, 20, 20]], [[1, 1, 1]] * 2, [30, 200], [20, 15], [0, np.inf])
veh = Vehicle("t", 300, 200, 200, payload=1500)
p = planner.plan_load(cg, veh, 0.8)
ok("payload binds and is kept", valid(p) and p.weight <= 1500 and p.left.sum() > 0, (p.weight, p.left.sum()))
fr = p.boxes[p.boxes[:, 6] == 0]
above = [(b2[2] == b1[5]) and min(b1[3], b2[3]) > max(b1[0], b2[0]) and min(b1[4], b2[4]) > max(b1[1], b2[1])
         for b1 in fr for b2 in p.boxes]
ok("nothing on top of fragile boxes", not any(above))
bad = 0
for it in range(8):                                   # no weights given, some boxes fragile
    T = int(rng.integers(3, 8))
    cg = Cargo(rng.integers(20, 80, size=(T, 3)), np.ones((T, 3), bool), rng.integers(5, 40, size=T),
               bear=np.where(np.arange(T) < 2, 0.0, np.inf))
    bad += not valid(planner.plan_load(cg, Vehicle("t", 400, 230, 230), 0.5))
ok("without weights, fragile boxes still carry nothing", bad == 0, bad)
cg = Cargo([[50, 50, 30]], [[0, 0, 1]], [60], [10], [25])
p = planner.plan_load(cg, Vehicle("t", 100, 100, 200), 0.5)
ok("identical boxes stack only as high as the limit allows (3 high)", valid(p) and p.boxes[:, 5].max() == 90,
   p.boxes[:, 5].max())

# ------------------------------------------------------------------------------------------------ drop-offs
print("Several drop-offs")
bad, plans = 0, []
for it in range(10):
    cg = random_load(rng, weights=bool(it % 2), stops=int(rng.integers(2, 5)))
    veh = Vehicle("t", 600, 240, 240, payload=9000)
    p = planner.plan_load(cg, veh, 0.8)
    bad += not valid(p)
    plans.append(p)
ok("random loads with 2-4 stops: every plan valid", bad == 0, bad)
cg = Cargo([[100, 100, 100], [100, 100, 100]], [[1, 1, 1]] * 2, [4, 4], stop=[1, 2])
p = planner.plan_load(cg, Vehicle("t", 400, 100, 200), 0.5)
xs = {s: p.boxes[p.stops == s][:, 0] for s in (1, 2)}
ok("the last stop goes deepest", p.left.sum() == 0 and xs[2].max() < xs[1].min(), xs)


def order_ok(boxes):
    b = boxes
    ox = np.minimum(b[:, None, 3], b[None, :, 3]) > np.maximum(b[:, None, 0], b[None, :, 0])
    oy = np.minimum(b[:, None, 4], b[None, :, 4]) > np.maximum(b[:, None, 1], b[None, :, 1])
    oz = np.minimum(b[:, None, 5], b[None, :, 5]) > np.maximum(b[:, None, 2], b[None, :, 2])
    rests = (b[None, :, 5] == b[:, None, 2]) & ox & oy
    behind = (b[None, :, 3] <= b[:, None, 0]) & oy & oz
    i, j = np.nonzero(rests | behind)
    return bool((j < i).all())                     # boxes are stored in loading order


ok("loading order: every box after what it rests on and what is behind it",
   all(order_ok(p.boxes) for p in plans[:6]))

# ------------------------------------------------------------------------------------------------ by hand
print("By hand, balance, fleet")
bad = 0
for it in range(12):
    cg = random_load(rng, weights=True, stops=int(rng.integers(1, 4)))
    bad += not valid(planner.by_hand(cg, Vehicle("t", 600, 240, 240, payload=7000)))
for num, cg, veh in brdata.load(10)[:3]:
    bad += not valid(planner.by_hand(cg, veh))
ok("by-hand plans are valid too", bad == 0, bad)
dc = demo.cargo_from_table(demo.boxes_table())
mt = demo.vehicle("Medium truck (6.1 m)")
dp, dh = planner.plan_load(dc, mt, 3.0), planner.by_hand(dc, mt)
ok("demo: the planner loads all 198 boxes in the medium truck", valid(dp) and dp.left.sum() == 0,
   dp.loaded.sum())
ok("demo: by hand leaves boxes behind", valid(dh) and dh.left.sum() > 0, dh.left.sum())
ok("demo: this-side-up items stand as labelled", bool(dc.upright[0].tolist() == [False, False, True])
   and bool(dc.upright[[i for i, n in enumerate(dc.names) if n.startswith("Pedestal")][0]].all()))
big = demo.vehicle("40 ft container")
p40 = planner.plan_load(dc, big, 2.0)
p40c = planner.centred(p40)
b40, b40c = p40.balance(), p40c.balance()
ok("part load moved to the middle: still valid, balance point near the middle",
   valid(p40c) and abs(b40c["from_middle"]) <= 1 and b40c["half_rule"], b40c)
ok("part load: lower stacks are tried when the weight sits forward",
   p40.left.sum() == 0 and ("stack_height_limit" in p40.info or b40["half_rule"]))
fake = planner.Plan(Vehicle("t", 600, 200, 200), Cargo([[100, 100, 100], [100, 100, 100]], [[1, 1, 1]] * 2, [1, 3],
                                                       [900, 1]), np.array([[0, 0, 0, 100, 100, 100, 0],
                                                                            [100, 0, 0, 200, 100, 100, 1],
                                                                            [200, 0, 0, 300, 100, 100, 1],
                                                                            [300, 0, 0, 400, 100, 100, 1]]))
fc = planner.centred(fake)
ok("moving a load never pushes it out of the door", valid(fc) and fc.boxes[:, 3].max() <= 600, fc.boxes[:, 3].max())
vs = demo.vehicles_from_table(demo.vehicles_table())
fp, fl = planner.plan_fleet(dc, vs, 1.0)
hp, hl = planner.plan_fleet(dc, vs, 0, method="by hand")
got = sum(p.loaded for p in fp) + fl
ok("fleet: every box is planned exactly once or reported", bool((got == dc.count).all()))
ok("fleet: every trip is valid", all(valid(p) for p in fp + hp))
ok("fleet: planner costs no more than by hand on the demo", sum(p.vehicle.cost for p in fp) <=
   sum(p.vehicle.cost for p in hp), (sum(p.vehicle.cost for p in fp), sum(p.vehicle.cost for p in hp)))
small = [Vehicle("Van", 330, 170, 180, 1200, 60, 1)]                 # one van only
fp2, fl2 = planner.plan_fleet(dc, small, 0.5)
got2 = sum(p.loaded for p in fp2) + fl2
ok("fleet with too few vehicles: what fits nowhere is reported", bool((got2 == dc.count).all()) and fl2.sum() > 0
   and len(fp2) <= small[0].available, (len(fp2), fl2.sum()))

# ------------------------------------------------------------------------------------------------ files and pages
print("Workbook, 3D page, PDF")
bx, vh = workbook.read(io.BytesIO(workbook.template_bytes()))
ok("template reads back to the same load", demo.cargo_from_table(bx).count.sum() == dc.count.sum()
   and len(demo.vehicles_from_table(vh)) == len(demo.VEHICLES))
for name, df, word in [
        ("text in a size", pd.DataFrame({"Item": ["a"], "Length (cm)": ["big"], "Width (cm)": [1], "Height (cm)": [1],
                                        "Quantity": [1]}), "numbers"),
        ("a size of zero", pd.DataFrame({"Item": ["a"], "Length (cm)": [0], "Width (cm)": [1], "Height (cm)": [1],
                                        "Quantity": [1]}), "above zero"),
        ("a missing column", pd.DataFrame({"Item": ["a"], "Length (cm)": [1]}), "missing column"),
        ("nothing to load", pd.DataFrame({"Item": ["a"], "Length (cm)": [1], "Width (cm)": [1], "Height (cm)": [1],
                                         "Quantity": [0]}), "no boxes")]:
    try:
        demo.cargo_from_table(df)
        ok(f"bad load list refused: {name}", False, "accepted")
    except ValueError as e:
        ok(f"bad load list refused: {name}", word in str(e), str(e))
m = workbook.manifest_bytes([dp], demo.STOPS, dp.left, dc)
sheets = pd.read_excel(io.BytesIO(m), sheet_name=None)
ok("manifest has a row per box in loading order", len(sheets["Loading steps"]) == len(dp.boxes) and
   list(sheets["Loading steps"]["Step"]) == list(range(1, len(dp.boxes) + 1)))
two = planner.Plan(V, C, good)
ok("steps say what each box rests on", [r["Rests on"] for r in report.steps_table(two)] == ["floor", "1"])
evil = Cargo([[50, 50, 50]], [[1, 1, 1]], [2], names=["</script><script>alert(1)</script>"])
pe = planner.plan_load(evil, Vehicle("t", 200, 200, 200), 0.3)
page = viewer.html(pe)
ok("item names cannot break out of the 3D page", "</script><script>alert" not in page and page.count("</script>") == 1)
ok("3D page carries every box", f'"boxes":{pe.boxes[:, :6].tolist()}'.replace(" ", "") in page.replace(" ", ""))
pdf = report.pdf(dp, "Demo", demo.STOPS, group=24, by_hand=dh)
try:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(pdf))
    ok("PDF: a cover and one page per 24 steps", len(r.pages) == 1 + math.ceil(len(dp.boxes) / 24), len(r.pages))
    txt = r.pages[1].extract_text()
    ok("PDF: steps pages list the steps", "Steps 1" in txt and "Rests on" in txt)
except ImportError:
    ok("PDF builds", pdf[:4] == b"%PDF")

print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    print("FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
