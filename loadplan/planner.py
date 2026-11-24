"""
Load plans for real loads, on top of the engine: drop-off order over several stops, how many vehicles of which kind,
weight balance, and a simple by-hand plan (rows of stacks, wall after wall) to compare with.

Several stops: the goods for the last stop are packed first, deepest in the vehicle; then the free space behind or
beside them in the same lane is closed to earlier stops (a box for an earlier stop may go in front of a later one or
on top of it, never behind it), and the next stop is packed into what is left, and so on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .engine import (INF, WIDTHS, Cargo, Packer, State, Vehicle, _cut, _mind, block_boxes, expand, loading_order,
                     make_blocks, orientations)


@dataclass
class Plan:
    vehicle: Vehicle
    cargo: Cargo
    boxes: np.ndarray                    # (n, 7) x0 y0 z0 x1 y1 z1 type, in loading order
    seconds: float = 0.0
    method: str = "planner"
    info: dict = field(default_factory=dict)

    @property
    def loaded(self):
        return np.bincount(self.boxes[:, 6], minlength=self.cargo.T).astype(np.int64)

    @property
    def left(self):
        return self.cargo.count - self.loaded

    @property
    def volume(self):
        return int(((self.boxes[:, 3:6] - self.boxes[:, :3]).prod(axis=1)).sum())

    @property
    def fill(self):
        return self.volume / self.vehicle.volume

    @property
    def weight(self):
        return float(self.cargo.weight[self.boxes[:, 6]].sum())

    @property
    def depth(self):
        """How far from the front wall the load reaches (cm)."""
        return int(self.boxes[:, 3].max()) if len(self.boxes) else 0

    @property
    def stops(self):
        return self.cargo.stop[self.boxes[:, 6]]

    def balance(self):
        return balance(self)


def plan_load(cargo: Cargo, veh: Vehicle, time_limit=3.0, widths=WIDTHS, mode=None, spread=True) -> Plan:
    """The best load plan found for one vehicle within the time limit (seconds).

    The search is run with the usual way of choosing the next free space first (nearest a floor corner for one
    stop, back wall first for several); if it ends early, the time left goes to the other way, and the better plan
    is kept (more loaded, then better balanced, then shorter). spread: when everything fits but more than 60% of the
    weight would sit in one half of the length (a part load), plan again with lower stacks so the load runs further
    along the floor, and keep the tallest stacks that meet the 60% guide."""
    t0 = time.time()
    multi = len({int(s) for s in cargo.stop[cargo.count > 0]}) > 1
    modes = [mode] if mode is not None else ([1, 0] if multi else [0, 1])
    best, tried = None, []
    for i, m in enumerate(modes):
        left = time_limit - (time.time() - t0)
        if i > 0 and left < 0.15 * time_limit:
            break
        plan = _plan_once(cargo, veh, left, widths, m)
        tried.append(m)
        if best is None or _rank(plan) > _rank(best):
            best = plan
    best.info["modes_tried"] = tried
    if spread and best.left.sum() == 0 and not _half_ok(best):
        tallest = int(max(min(h for (_, _, h) in orientations(cargo.dims[t], cargo.upright[t]))
                          for t in np.flatnonzero(cargo.count > 0)))
        for f in (0.8, 0.65, 0.5, 0.4, 0.3):
            cap = int(veh.H * f)
            if cap < tallest:
                break
            low = Vehicle(veh.name, veh.L, veh.W, cap, veh.payload, veh.cost, veh.available)
            p2 = _plan_once(cargo, low, max(0.3, time_limit / 4), widths, best.info["mode"])
            if p2.left.sum() == 0:
                p2.vehicle = veh
                p2.info["stack_height_limit"] = cap
                if _half_ok(p2):
                    best = p2
                    break
                if _front(p2) < _front(best):
                    best = p2
    best.seconds = time.time() - t0
    return best


def centred(plan: Plan) -> Plan:
    """The same load moved along the floor so that its balance point is as near the middle of the length as the
    room allows. Every rule still holds (the boxes keep their places relative to each other); the gap left at the
    front wall must be blocked or braced."""
    b = plan.boxes.copy()
    if not len(b):
        return plan
    w = plan.cargo.weight[b[:, 6]]
    if w.sum() <= 0:
        w = (b[:, 3:6] - b[:, :3]).prod(axis=1).astype(float)
    cog = float((w * (b[:, 0] + b[:, 3]) / 2).sum() / w.sum())
    room = plan.vehicle.L - int(b[:, 3].max())
    shift = int(np.clip(round(plan.vehicle.L / 2 - cog), 0, room))
    b[:, [0, 3]] += shift
    info = dict(plan.info, gap_at_front_wall=shift)
    return Plan(plan.vehicle, plan.cargo, b, plan.seconds, plan.method, info)


def _front(plan):
    """Larger share of the weight (or volume, without weights) in one half of the length."""
    b = plan.boxes
    if not len(b):
        return 0.5
    w = plan.cargo.weight[b[:, 6]]
    if w.sum() <= 0:
        w = (b[:, 3:6] - b[:, :3]).prod(axis=1).astype(float)
    mid = plan.vehicle.L / 2
    inside = np.clip((mid - b[:, 0]) / (b[:, 3] - b[:, 0]), 0, 1)
    f = float((w * inside).sum() / w.sum())
    return max(f, 1 - f)


def _half_ok(plan):
    return _front(plan) <= 0.60 + 1e-9


def _rank(plan):
    bal = plan.balance()
    ok = 0 if bal is None else int(bal["half_rule"]) + int(bal["sideways_ok"]) + int(bal["height_ok"])
    return (plan.volume, ok, -plan.depth)


def _plan_once(cargo: Cargo, veh: Vehicle, time_limit, widths, mode) -> Plan:
    t0 = time.time()
    deadline = t0 + time_limit
    stops = sorted({int(s) for s in cargo.stop[cargo.count > 0]}, reverse=True)
    vol_t = (cargo.dims.prod(axis=1) * cargo.count).astype(float)
    tmin = cargo.dims.min(axis=1).astype(np.int64)
    st = State.empty(cargo, veh)
    parts, info = [], {"widths": {}, "stops": stops, "mode": mode}
    for n, g in enumerate(stops):
        mask = cargo.stop == g
        group = cargo.with_counts(np.where(mask, st.rem, 0))
        bl = make_blocks(group, veh)
        start = State(st.sp, st.cap, st.rem.copy(), st.wleft)
        pk = Packer(cargo, veh, bl, start, mode)
        later = np.isin(cargo.stop, stops[n:])            # this stop and the ones still to pack
        budget = max(0.1, (deadline - time.time()) * vol_t[mask].sum() / max(vol_t[later].sum(), 1.0))
        _, placed, inf = pk.solve(budget, widths)
        info["widths"][g] = inf["widths"]
        parts.append(expand(placed, bl))
        if n == len(stops) - 1:
            break
        after, psp, pcap = pk.replay(placed)
        rem = after.rem.copy()
        rem[mask] = 0                              # whatever of this stop did not fit stays off this vehicle
        mind = _mind(rem, tmin)
        taken = block_boxes(placed, bl)
        sp, cap = after.sp, after.cap
        if len(psp):                               # spaces set aside while packing this stop come back
            sp, cap = np.vstack([sp, psp]), np.concatenate([cap, pcap])
        lane = taken.copy()
        lane[:, 0] = 0                             # nothing for an earlier stop behind these boxes (nor in them:
        sp, cap = _cut(sp, cap, lane, mind)        # this also takes the new boxes out of the spaces set aside)
        st = State(sp, cap, rem, after.wleft)
    boxes = np.vstack(parts) if parts else np.zeros((0, 7), np.int64)
    boxes = boxes[loading_order(boxes, cargo.stop[boxes[:, 6]])]
    return Plan(veh, cargo, boxes, time.time() - t0, "planner", info)


def by_hand(cargo: Cargo, veh: Vehicle) -> Plan:
    """What a careful loader does without a plan: the last stop first and the biggest boxes first; each stack starts
    on the floor as far back as it goes (turned if that gets it further back), then smaller boxes of the same stop go
    on top while they fit on it and the weight rules allow."""
    t0 = time.time()
    L, W, H = veh.L, veh.W, veh.H
    rem = cargo.count.copy()
    wleft = float(min(veh.payload, 1e17))
    wt, br = cargo.weight, cargo.bear

    def oris(t):
        o = orientations(cargo.dims[t], cargo.upright[t])
        lwh = tuple(int(v) for v in cargo.dims[t])
        return ([lwh] if lwh in o else []) + [v for v in o if v != lwh]

    def can_stack(col, t, l, w):
        """col: [(type, l, w, z1)] bottom to top. May a box of type t (footprint l x w) go on top?"""
        loads = [0.0] * len(col)
        tot = [float(wt[c[0]]) for c in col] + [float(wt[t])]
        areas = [c[1] * c[2] for c in col] + [l * w]
        for k in range(len(col) - 1, -1, -1):           # from the top down: each box carries everything above it
            loads[k] = sum(tot[k + 1:])
            press = (float(wt[col[k + 1][0]]) + (loads[k + 1] if k + 1 < len(col) else 0.0)) / areas[k + 1] \
                if k + 1 < len(col) else tot[-1] / areas[-1]
            if loads[k] > br[col[k][0]] + 1e-9 or press * areas[k] > br[col[k][0]] + 1e-9:
                return False
        return True

    out = []
    sky = np.zeros(W, np.int64)                         # how far from the front wall the floor is taken, per cm across
    for g in sorted({int(s) for s in cargo.stop[cargo.count > 0]}, reverse=True):
        mine = sorted((t for t in range(cargo.T) if cargo.stop[t] == g),
                      key=lambda t: (-int(cargo.dims[t].prod()), t))
        for t in mine:
            while rem[t] > 0 and wt[t] <= wleft:
                best = None                             # the spot furthest back (then nearest the left wall)
                for (l, w, h) in oris(t):
                    if w > W or h > H:
                        continue
                    need = np.lib.stride_tricks.sliding_window_view(sky, w).max(axis=1)
                    y = int(np.argmin(need))
                    x = int(need[y])
                    if x + l <= L and (best is None or (x, y) < best[:2]):
                        best = (x, y, l, w, h)
                if best is None:
                    break                               # no room left for this kind
                x, y, l, w, h = best
                col = [(t, l, w, h)]
                out.append((x, y, 0, x + l, y + w, h, t))
                rem[t] -= 1
                wleft -= wt[t]
                while True:
                    _, tl, tw, z = col[-1]
                    nxt = None
                    for u in sorted(mine, key=lambda u: u != col[-1][0]):      # the same kind first
                        if rem[u] <= 0 or wt[u] > wleft:
                            continue
                        for (l2, w2, h2) in oris(u):
                            if l2 <= tl and w2 <= tw and z + h2 <= H and can_stack(col, u, l2, w2):
                                nxt = (u, l2, w2, h2)
                                break
                        if nxt:
                            break
                    if nxt is None:
                        break
                    u, l2, w2, h2 = nxt
                    out.append((x, y, z, x + l2, y + w2, z + h2, u))
                    col.append((u, l2, w2, z + h2))
                    rem[u] -= 1
                    wleft -= wt[u]
                sky[y:y + w] = x + l
    boxes = np.array(out, np.int64).reshape(-1, 7)
    boxes = boxes[loading_order(boxes, cargo.stop[boxes[:, 6]])]
    return Plan(veh, cargo, boxes, time.time() - t0, "by hand")


def plan_fleet(cargo: Cargo, vehicles: list, time_limit=2.0, max_trips=30, method="planner"):
    """Vehicles to carry everything, one after the other: if one vehicle can take all that is left, the cheapest
    that does, unless filling the vehicle that carries most for its cost now and sending the rest later looks
    cheaper; otherwise that best-value vehicle, filled as well as possible. Returns the plans and the counts that
    could not be loaded at all. method "by hand" fills each vehicle with by_hand instead."""
    if method == "by hand":
        def plan_load(c, v, _t):                    # noqa: F811 - same choice of vehicles, loaded by hand
            return by_hand(c, v)
    else:
        plan_load = globals()["plan_load"]
    remaining = cargo.count.copy()
    used = [0] * len(vehicles)
    plans = []
    while remaining.sum() > 0 and len(plans) < max_trips:
        avail = [i for i, v in enumerate(vehicles) if used[i] < v.available]
        if not avail:
            break
        rest = cargo.with_counts(remaining)
        rv, rw = rest.volume, float((cargo.weight * remaining).sum())
        chosen = None
        for i in sorted(avail, key=lambda i: (vehicles[i].cost, vehicles[i].volume)):
            v = vehicles[i]
            if rv <= v.volume and rw <= v.payload:
                pl = plan_load(rest, v, time_limit)
                if pl.left.sum() == 0:
                    chosen = (i, pl)
                    break
        def worth(i):
            v = vehicles[i]
            carry = min(v.volume, rv) * (min(1.0, v.payload / rw) if rw > 0 else 1.0)
            return (carry / max(v.cost, 1e-9), v.volume)
        best_i = max(avail, key=worth)
        cheapest = min(vehicles[j].cost for j in avail)
        if chosen is not None and best_i != chosen[0] and vehicles[best_i].cost + cheapest < vehicles[chosen[0]].cost:
            # one big vehicle for everything, or a better-value one now and the rest later: the cheaper estimate
            pl = plan_load(rest, vehicles[best_i], time_limit)
            if pl.loaded.sum() > 0:
                left = remaining - pl.loaded
                lv, lw = int((cargo.dims.prod(axis=1) * left).sum()), float((cargo.weight * left).sum())
                fits = [vehicles[j].cost for j in avail if vehicles[j].volume >= lv and vehicles[j].payload >= lw]
                big = max(vehicles[j].volume for j in avail)
                rest_cost = min(fits) if fits else cheapest * int(np.ceil(lv / big))
                if vehicles[best_i].cost + rest_cost < vehicles[chosen[0]].cost:
                    chosen = (best_i, pl)
        if chosen is None:
            i = best_i
            pl = plan_load(rest, vehicles[i], time_limit)
            if pl.loaded.sum() == 0:
                others = [j for j in avail if j != i]
                pl, i = next(((p, j) for j in others for p in [plan_load(rest, vehicles[j], time_limit)]
                              if p.loaded.sum() > 0), (pl, i))
                if pl.loaded.sum() == 0:
                    break                           # nothing left fits any vehicle
            chosen = (i, pl)
        i, pl = chosen
        used[i] += 1
        plans.append(pl)
        remaining = remaining - pl.loaded
    return plans, remaining


def balance(plan: Plan):
    """Where the weight sits. Guidance from the CTU Code (IMO/ILO/UNECE): the centre of gravity near the middle of
    the length and on the centreline, below half the height, and no more than 60% of the weight in either half of
    the length. x = 0 is the front wall (cab end)."""
    b = plan.boxes
    veh = plan.vehicle
    w = plan.cargo.weight[b[:, 6]] if len(b) else np.zeros(0)
    if not len(b) or w.sum() <= 0:
        return None
    c = (b[:, :3] + b[:, 3:6]) / 2
    cog = (w[:, None] * c).sum(axis=0) / w.sum()
    mid = veh.L / 2
    in_front = np.clip((mid - b[:, 0]) / (b[:, 3] - b[:, 0]), 0, 1)
    front = float((w * in_front).sum() / w.sum())
    side = float(cog[1] - veh.W / 2)
    return {
        "cog": [float(v) for v in cog],
        "from_middle": float(cog[0] - mid),          # cm; negative: towards the front wall
        "front_half": front,
        "door_half": 1 - front,
        "half_rule": max(front, 1 - front) <= 0.60 + 1e-9,
        "sideways": side,                            # cm off the centreline; positive: towards the far side
        "sideways_ok": abs(side) <= 0.05 * veh.W,
        "height_ok": cog[2] <= veh.H / 2,
    }


__all__ = ["Plan", "plan_load", "by_hand", "plan_fleet", "balance", "centred", "INF"]
