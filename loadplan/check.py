"""
Checks of a load plan, box by box: inside the vehicle, no two boxes in the same place, every box fully supported
from below, only allowed orientations (this side up), no more boxes than there are, the payload, the weight on every
box within what it can bear (each box passes its own weight and its load down, shared by contact area), also when a
small box presses on one spot (the pressure under any box resting on it, times its top area, within its limit), and
the drop-off order (no box for a later stop between a box and the door, or on top of it).
"""

from __future__ import annotations

import numpy as np

from .engine import Cargo, Vehicle


def loads_on(boxes, weight):
    """Weight resting on each box (kg), passed down from the top through the contact areas, and the highest pressure
    (kg/cm2) under any box resting on it."""
    n = len(boxes)
    b = boxes
    ox = (np.minimum(b[:, None, 3], b[None, :, 3]) - np.maximum(b[:, None, 0], b[None, :, 0])).clip(0)
    oy = (np.minimum(b[:, None, 4], b[None, :, 4]) - np.maximum(b[:, None, 1], b[None, :, 1])).clip(0)
    contact = np.where(b[None, :, 5] == b[:, None, 2], ox * oy, 0).astype(float)    # [i, j]: i rests on j
    np.fill_diagonal(contact, 0)
    total = np.asarray(weight, float).copy()                   # own weight + load from above
    on = np.zeros(n)
    press = np.zeros(n)
    for i in np.argsort(-b[:, 2], kind="stable"):              # from the top down
        area = contact[i].sum()
        if area > 0:
            share = total[i] * contact[i] / area
            on += share
            total += share
            press = np.where(contact[i] > 0, np.maximum(press, total[i] / area), press)
    return on, press


def problems(boxes, cargo: Cargo, veh: Vehicle, support=1.0):
    """List of problems (empty if the plan is valid). boxes: (n, 7) x0 y0 z0 x1 y1 z1 type."""
    errs = []
    if not len(boxes):
        return errs
    b = boxes[:, :6]
    t = boxes[:, 6]
    if (b[:, :3] < 0).any() or (b[:, 3] > veh.L).any() or (b[:, 4] > veh.W).any() or (b[:, 5] > veh.H).any():
        errs.append("a box sticks out of the vehicle")
    ov = ((np.minimum(b[:, None, 3], b[None, :, 3]) - np.maximum(b[:, None, 0], b[None, :, 0])).clip(0) *
          (np.minimum(b[:, None, 4], b[None, :, 4]) - np.maximum(b[:, None, 1], b[None, :, 1])).clip(0) *
          (np.minimum(b[:, None, 5], b[None, :, 5]) - np.maximum(b[:, None, 2], b[None, :, 2])).clip(0))
    np.fill_diagonal(ov, 0)
    if ov.max() > 0:
        errs.append("two boxes overlap")
    ox = (np.minimum(b[:, None, 3], b[None, :, 3]) - np.maximum(b[:, None, 0], b[None, :, 0])).clip(0)
    oy = (np.minimum(b[:, None, 4], b[None, :, 4]) - np.maximum(b[:, None, 1], b[None, :, 1])).clip(0)
    under = np.where(b[None, :, 5] == b[:, None, 2], ox * oy, 0).sum(axis=1)
    foot = (b[:, 3] - b[:, 0]) * (b[:, 4] - b[:, 1])
    bad = (b[:, 2] > 0) & (under < support * foot - 1e-6)
    if bad.any():
        errs.append(f"{int(bad.sum())} box(es) not fully supported")
    dims = b[:, 3:] - b[:, :3]
    for d, k in zip(dims, t):                                # same three sides, and the one up may stand up
        sides = [int(v) for v in cargo.dims[k]]
        up = [int(sides[i]) for i in range(3) if cargo.upright[k][i]]
        if sorted(int(v) for v in d) != sorted(sides) or int(d[2]) not in up:
            errs.append("a box stands in a way it may not")
            break
    used = np.bincount(t, minlength=cargo.T)
    if (used > cargo.count).any():
        errs.append("more boxes than there are")
    w = cargo.weight[t]
    if w.sum() > veh.payload + 1e-6:
        errs.append("over the payload")
    on, press = loads_on(boxes, w)
    bear = cargo.bear[t]
    ox = (np.minimum(b[:, None, 3], b[None, :, 3]) > np.maximum(b[:, None, 0], b[None, :, 0]))
    oy = (np.minimum(b[:, None, 4], b[None, :, 4]) > np.maximum(b[:, None, 1], b[None, :, 1]))
    carried = ((b[None, :, 2] == b[:, None, 5]) & ox & oy).any(axis=1)      # something rests on the box
    if (carried & (bear <= 0)).any():
        errs.append("something rests on a box that must have nothing on top")
    over = (on > bear + 1e-6) | (press * foot > bear * (1 + 1e-9) + 1e-6)
    if over.any():
        errs.append(f"{int(over.sum())} box(es) carry more than they can bear")
    stop = cargo.stop[t]
    oz = (np.minimum(b[:, None, 5], b[None, :, 5]) > np.maximum(b[:, None, 2], b[None, :, 2]))
    oyy = (np.minimum(b[:, None, 4], b[None, :, 4]) > np.maximum(b[:, None, 1], b[None, :, 1]))
    oxx = (np.minimum(b[:, None, 3], b[None, :, 3]) > np.maximum(b[:, None, 0], b[None, :, 0]))
    later = stop[None, :] > stop[:, None]                       # j is unloaded after i
    in_front = (b[None, :, 0] >= b[:, None, 3]) & oyy & oz      # j between i and the door
    on_top = (b[None, :, 2] >= b[:, None, 5]) & oxx & oyy       # j above i
    if (later & (in_front | on_top)).any():
        errs.append("a box for a later stop blocks one for an earlier stop")
    return errs
