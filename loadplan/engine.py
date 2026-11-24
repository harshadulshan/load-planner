"""
The packing engine: which boxes go where in a truck or container.

Block building on maximal free spaces (Parreño et al. 2008, Fanslau & Bortfeldt 2010): boxes of one kind are grouped
into blocks (a column, a layer, a stack of identical boxes, or two such blocks side by side or one on the other), and
blocks are placed one at a time into the free space nearest the back wall. Every placement is fully supported from
below: the free space above a block covers only that block's top, and free spaces at the same height that touch are
joined (so a block can bridge two stacks of equal height, as in Gonçalves & Resende 2012).

Weight. Each box type says how many kg one box can carry on top, spread over its top; that is a pressure (kg/cm2)
it can take. Every free space keeps the extra pressure its floor can still take along any vertical line down to the
vehicle floor: a block goes in only if the pressure under it is within that allowance, and the space above the block
gets the allowance minus that pressure (or the block's own, if lower). Boxes resting on a box pass their weight on
spread over that box's base, so this keeps both the kg on every box and the pressure from any box resting on it
within limits, for any mix of box sizes; check.py verifies the result box by box. The vehicle's payload is never
exceeded.

The search is a pilot method with growing width: at each step the K largest blocks that fit are tried, each trial is
finished greedily (largest block first) and the block whose finish fills most is kept (ties: the shorter load);
K doubles while time is left. The inner loops are compiled with numba.

Units: centimetres and kilograms. Axes: x from the back wall (0) to the door, y across, z up.
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field

import numba as nb
import numpy as np

INF = 1e18
WIDTHS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
# block table columns
DX, DY, DZ, TX, TY, T1, N1, T2, N2, VOL = range(10)


# ------------------------------------------------------------------------------------------------ problem
@dataclass
class Cargo:
    """Box types. dims (T, 3) length, width, height (cm); upright (T, 3): may that dimension stand vertical?
    count (T,); weight (T,) kg per box; bear (T,) kg one box can carry on top (inf: no limit, 0: nothing on top);
    stop (T,) drop-off order (1 = unloaded first); names (T,)."""
    dims: np.ndarray
    upright: np.ndarray
    count: np.ndarray
    weight: np.ndarray = None
    bear: np.ndarray = None
    stop: np.ndarray = None
    names: list = None

    def __post_init__(self):
        T = len(self.dims)
        self.dims = np.asarray(self.dims, np.int64).reshape(T, 3)
        self.upright = np.asarray(self.upright, bool).reshape(T, 3)
        self.count = np.asarray(self.count, np.int64).reshape(T)
        self.weight = np.zeros(T) if self.weight is None else np.asarray(self.weight, float).reshape(T)
        self.bear = np.full(T, np.inf) if self.bear is None else np.asarray(self.bear, float).reshape(T)
        self.stop = np.ones(T, np.int64) if self.stop is None else np.asarray(self.stop, np.int64).reshape(T)
        self.names = [f"type {i + 1}" for i in range(T)] if self.names is None else list(self.names)

    @property
    def T(self):
        return len(self.dims)

    def with_counts(self, counts):
        return Cargo(self.dims, self.upright, np.asarray(counts, np.int64), self.weight, self.bear, self.stop,
                     self.names)

    @property
    def volume(self):
        return int((self.dims.prod(axis=1) * self.count).sum())


@dataclass
class Vehicle:
    name: str
    L: int
    W: int
    H: int
    payload: float = np.inf          # kg
    cost: float = 1.0                # per trip (fleet planning)
    available: int = 99              # how many of this kind (fleet planning)

    @property
    def volume(self):
        return self.L * self.W * self.H


def orientations(dims, upright):
    """(l, w, h) placements allowed: h must be a dimension that may stand vertical."""
    out = set()
    for i in range(3):
        if upright[i]:
            a, b = [int(dims[j]) for j in range(3) if j != i]
            out.add((a, b, int(dims[i])))
            out.add((b, a, int(dims[i])))
    return sorted(out)


# ------------------------------------------------------------------------------------------------ blocks
@dataclass
class Blocks:
    B: np.ndarray                    # (nb, 10) int64: dx dy dz tx ty t1 n1 t2 n2 vol
    w: np.ndarray                    # (nb,) weight (kg)
    p: np.ndarray                    # (nb,) highest pressure under the block (kg/cm2)
    a: np.ndarray                    # (nb,) extra pressure its top can take (INF: any, -1: nothing at all)
    kind: np.ndarray                 # 0 simple, 1 two along x, 2 two along y, 3 one on the other
    parts: np.ndarray                # (nb, 2) simple-block rows of the parts (kind > 0)
    simple: np.ndarray               # (ns, 7) t l w h nx ny nz of the simple blocks

    def simple_dims(self, i):
        t, l, w, h, nx, ny, nz = self.simple[int(i)]
        return np.array([l * nx, w * ny, h * nz])


def make_blocks(cargo: Cargo, veh: Vehicle, max_blocks=12000, pairs=500, min_fill=0.98):
    L, W, H = veh.L, veh.W, veh.H
    rows, srows, wts, prs, als = [], [], [], [], []
    for t in range(cargo.T):
        q = int(cargo.count[t])
        if q <= 0:
            continue
        wb, bear = float(cargo.weight[t]), float(cargo.bear[t])
        if bear <= 0:
            zmax = 1
        elif np.isinf(bear) or wb <= 0:
            zmax = q
        else:
            zmax = 1 + int(np.floor(bear / wb + 1e-9))
        for (l, w, h) in orientations(cargo.dims[t], cargo.upright[t]):
            if l > L or w > W or h > H:
                continue
            area = float(l * w)
            for nz in range(1, min(q, H // h, zmax) + 1):
                if np.isinf(bear):
                    allow = INF
                elif bear <= 0:
                    allow = -1.0
                else:
                    allow = (bear - (nz - 1) * wb) / area
                for ny in range(1, min(q // nz, W // w) + 1):
                    for nx in range(1, min(q // (nz * ny), L // l) + 1):
                        n = nx * ny * nz
                        rows.append((l * nx, w * ny, h * nz, l * nx, w * ny, t, n, -1, 0, l * w * h * n))
                        srows.append((t, l, w, h, nx, ny, nz))
                        wts.append(wb * n)
                        prs.append(wb * nz / area)
                        als.append(allow)
    if not rows:
        return Blocks(np.zeros((0, 10), np.int64), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0, np.int64),
                      np.zeros((0, 2), np.int64), np.zeros((0, 7), np.int64))
    B = np.array(rows, np.int64)
    S = np.array(srows, np.int64)
    Wt, Pr, Al = np.array(wts), np.array(prs), np.array(als)
    o = np.argsort(-B[:, VOL], kind="stable")
    B, S, Wt, Pr, Al = B[o], S[o], Wt[o], Pr[o], Al[o]
    ns = len(B)
    kind = np.zeros(ns, np.int64)
    parts = np.tile(np.arange(ns)[:, None], (1, 2))
    gen, gw, gpr, gal, gk, gp = [], [], [], [], [], []

    def add(row, w_, p_, a_, k_, pr_):
        gen.append(row); gw.append(w_); gpr.append(p_); gal.append(a_); gk.append(k_); gp.append(pr_)

    top_n = min(ns, pairs)
    for i in range(top_n):
        a = B[i]
        j = np.arange(i, top_n)
        c = B[j]
        same_t = c[:, T1] == a[T1]
        cnt_ok = np.where(same_t, cargo.count[a[T1]] >= a[N1] + c[:, N1], True)
        m = (c[:, DY] == a[DY]) & (c[:, DZ] == a[DZ]) & (a[DX] + c[:, DX] <= L) & cnt_ok
        for jj in j[m]:
            b = B[jj]
            add((a[DX] + b[DX], a[DY], a[DZ], a[DX] + b[DX], a[DY], a[T1], a[N1], b[T1], b[N1], a[VOL] + b[VOL]),
                Wt[i] + Wt[jj], max(Pr[i], Pr[jj]), min(Al[i], Al[jj]), 1, (i, jj))
        m = (c[:, DX] == a[DX]) & (c[:, DZ] == a[DZ]) & (a[DY] + c[:, DY] <= W) & cnt_ok
        for jj in j[m]:
            b = B[jj]
            add((a[DX], a[DY] + b[DY], a[DZ], a[DX], a[DY] + b[DY], a[T1], a[N1], b[T1], b[N1], a[VOL] + b[VOL]),
                Wt[i] + Wt[jj], max(Pr[i], Pr[jj]), min(Al[i], Al[jj]), 2, (i, jj))
        jj = j[j != i]
        for bot_a in (True, False):                          # the other block on a, or a on the other block
            lo = np.full(len(jj), i) if bot_a else jj
            up = jj if bot_a else np.full(len(jj), i)
            A_, U = B[lo], B[up]
            ok = (U[:, DX] <= A_[:, TX]) & (U[:, DY] <= A_[:, TY]) & (A_[:, DZ] + U[:, DZ] <= H)
            same = A_[:, T1] == U[:, T1]
            ok &= ~same | (cargo.count[A_[:, T1]] >= A_[:, N1] + U[:, N1])
            vol = A_[:, DX] * A_[:, DY] * (A_[:, DZ] + U[:, DZ])
            ok &= (A_[:, VOL] + U[:, VOL]) >= min_fill * vol
            ok &= Al[lo] >= Pr[up]
            for k in np.flatnonzero(ok):
                aa, uu, bo, uo = A_[k], U[k], int(lo[k]), int(up[k])
                add((aa[DX], aa[DY], aa[DZ] + uu[DZ], uu[DX], uu[DY], aa[T1], aa[N1], uu[T1], uu[N1],
                     aa[VOL] + uu[VOL]),
                    Wt[bo] + Wt[uo], Pr[bo] + Pr[uo], min(Al[uo], Al[bo] - Pr[uo]), 3, (bo, uo))
    if gen:
        B = np.vstack([B, np.array(gen, np.int64)])
        Wt = np.concatenate([Wt, gw])
        Pr = np.concatenate([Pr, gpr])
        Al = np.concatenate([Al, gal])
        kind = np.concatenate([kind, gk])
        parts = np.vstack([parts, np.array(gp, np.int64)])
    # identical blocks once; largest first
    key = np.column_stack([B, kind])
    _, first = np.unique(key, axis=0, return_index=True)
    first = np.sort(first)
    B, Wt, Pr, Al, kind, parts = B[first], Wt[first], Pr[first], Al[first], kind[first], parts[first]
    o = np.argsort(-B[:, VOL], kind="stable")[:max_blocks]
    Al = np.where(Al >= INF / 2, INF, Al)
    return Blocks(B[o], Wt[o], Pr[o], Al[o], kind[o], parts[o], S)


# ------------------------------------------------------------------------------------------------ compiled core
@nb.njit(cache=True)
def _pick(sp, n, W, mode):
    """The next free space to fill. mode 0: nearest a corner of the vehicle floor (the three distances to the back
    wall, a side wall and the floor, smallest first; Parreño et al. 2008); mode 1: nearest the back wall, then a side
    wall or the floor (wall after wall, for loads with several drop-offs). Ties: the larger space."""
    best, bk0, bk1, bk2, bv = -1, 0, 0, 0, -1
    for i in range(n):
        dy = min(sp[i, 1], W - sp[i, 4])
        a, b, c = sp[i, 0], dy, sp[i, 2]
        if mode == 0:
            if a > b:
                a, b = b, a
            if b > c:
                b, c = c, b
            if a > b:
                a, b = b, a
        elif b > c:
            b, c = c, b
        v = (sp[i, 3] - sp[i, 0]) * (sp[i, 4] - sp[i, 1]) * (sp[i, 5] - sp[i, 2])
        if best < 0 or (a, b, c) < (bk0, bk1, bk2) or ((a, b, c) == (bk0, bk1, bk2) and v > bv):
            best, bk0, bk1, bk2, bv = i, a, b, c, v
    return best


@nb.njit(cache=True)
def _fits(B, bw, bp, rem, s, cap, wleft, K, out):
    """Up to K blocks that fit space s (largest first, the table is sorted by volume); returns how many."""
    sx, sy, sz = s[3] - s[0], s[4] - s[1], s[5] - s[2]
    k = 0
    for i in range(B.shape[0]):
        if B[i, 0] > sx or B[i, 1] > sy or B[i, 2] > sz:
            continue
        if bw[i] > wleft or bp[i] > cap:
            continue
        t1, t2 = B[i, 5], B[i, 7]
        if t2 == t1:
            if rem[t1] < B[i, 6] + B[i, 8]:
                continue
        else:
            if rem[t1] < B[i, 6]:
                continue
            if t2 >= 0 and rem[t2] < B[i, 8]:
                continue
        out[k] = i
        k += 1
        if k >= K:
            break
    return k


@nb.njit(cache=True)
def _clean(sp, cap, n, mind):
    """Drop spaces smaller than the smallest box, duplicates and spaces inside another that can bear as much."""
    keep = np.ones(n, np.bool_)
    for i in range(n):
        if min(sp[i, 3] - sp[i, 0], min(sp[i, 4] - sp[i, 1], sp[i, 5] - sp[i, 2])) < mind:
            keep[i] = False
    for i in range(n):
        if not keep[i]:
            continue
        for j in range(n):
            if i == j or not keep[j]:
                continue
            if (sp[i, 0] >= sp[j, 0] and sp[i, 1] >= sp[j, 1] and sp[i, 2] >= sp[j, 2] and sp[i, 3] <= sp[j, 3]
                    and sp[i, 4] <= sp[j, 4] and sp[i, 5] <= sp[j, 5] and cap[i] <= cap[j]):
                same = True
                for c in range(6):
                    if sp[i, c] != sp[j, c]:
                        same = False
                if same and cap[i] == cap[j] and i < j:
                    continue                                    # identical: keep the first one
                keep[i] = False
                break
    m = 0
    for i in range(n):
        if keep[i]:
            m += 1
    osp = np.empty((m, 6), np.int64)
    oc = np.empty(m)
    m = 0
    for i in range(n):
        if keep[i]:
            osp[m] = sp[i]
            oc[m] = cap[i]
            m += 1
    return osp, oc


@nb.njit(cache=True)
def _join(sp, cap, n):
    """Spaces on the same floor height that touch side by side: add the space spanning both."""
    extra = np.empty((n * n * 2 + 1, 6), np.int64)
    ec = np.empty(n * n * 2 + 1)
    m = 0
    for i in range(n):
        for j in range(n):
            if i == j or sp[i, 2] != sp[j, 2]:
                continue
            top = min(sp[i, 5], sp[j, 5])
            c = min(cap[i], cap[j])
            if sp[i, 0] < sp[j, 0] and sp[i, 3] >= sp[j, 0] and sp[j, 3] > sp[i, 3]:
                y0, y1 = max(sp[i, 1], sp[j, 1]), min(sp[i, 4], sp[j, 4])
                if y1 > y0:
                    extra[m, 0], extra[m, 1], extra[m, 2] = sp[i, 0], y0, sp[i, 2]
                    extra[m, 3], extra[m, 4], extra[m, 5] = sp[j, 3], y1, top
                    ec[m] = c
                    m += 1
            if sp[i, 1] < sp[j, 1] and sp[i, 4] >= sp[j, 1] and sp[j, 4] > sp[i, 4]:
                x0, x1 = max(sp[i, 0], sp[j, 0]), min(sp[i, 3], sp[j, 3])
                if x1 > x0:
                    extra[m, 0], extra[m, 1], extra[m, 2] = x0, sp[i, 1], sp[i, 2]
                    extra[m, 3], extra[m, 4], extra[m, 5] = x1, sp[j, 4], top
                    ec[m] = c
                    m += 1
    if m == 0:
        return sp, cap
    osp = np.empty((n + m, 6), np.int64)
    oc = np.empty(n + m)
    osp[:n] = sp
    oc[:n] = cap
    osp[n:] = extra[:m]
    oc[n:] = ec[:m]
    return osp, oc


@nb.njit(cache=True)
def _split(sp, cap, ex0, ey0, ez0, ex1, ey1, ez1, out, oc):
    """Free spaces minus a box: spaces that miss it stay, the others keep their parts beside and below it."""
    m = 0
    for j in range(sp.shape[0]):
        c = cap[j]
        hit = (sp[j, 0] < ex1 and sp[j, 3] > ex0 and sp[j, 1] < ey1 and sp[j, 4] > ey0 and sp[j, 2] < ez1
               and sp[j, 5] > ez0)
        if not hit:
            out[m] = sp[j]
            oc[m] = c
            m += 1
            continue
        if sp[j, 0] < ex0:
            out[m] = sp[j]
            out[m, 3] = ex0
            oc[m] = c
            m += 1
        if sp[j, 3] > ex1:
            out[m] = sp[j]
            out[m, 0] = ex1
            oc[m] = c
            m += 1
        if sp[j, 1] < ey0:
            out[m] = sp[j]
            out[m, 4] = ey0
            oc[m] = c
            m += 1
        if sp[j, 4] > ey1:
            out[m] = sp[j]
            out[m, 1] = ey1
            oc[m] = c
            m += 1
        if sp[j, 2] < ez0:
            out[m] = sp[j]
            out[m, 5] = ez0
            oc[m] = c
            m += 1
    return m


@nb.njit(cache=True)
def _cut(sp, cap, obs, mind):
    """Remove boxes (rows x0 y0 z0 x1 y1 z1) from the free spaces."""
    for o in range(obs.shape[0]):
        n = sp.shape[0]
        out = np.empty((n * 5 + 1, 6), np.int64)
        oc = np.empty(n * 5 + 1)
        m = _split(sp, cap, obs[o, 0], obs[o, 1], obs[o, 2], obs[o, 3], obs[o, 4], obs[o, 5], out, oc)
        sp, cap = _clean(out[:m], oc[:m], m, mind)
    sp, cap = _join(sp, cap, sp.shape[0])
    return _clean(sp, cap, sp.shape[0], mind)


@nb.njit(cache=True)
def _place(sp, cap, si, b, B, bp, ba, W, mind):
    """Put block b into space si at its back corner nearest a side wall. Returns the new spaces, their allowances
    and the block's position (x0, y0, z0, right: set against the far side wall)."""
    n = sp.shape[0]
    s0, s1, s2, s3, s4, s5 = sp[si, 0], sp[si, 1], sp[si, 2], sp[si, 3], sp[si, 4], sp[si, 5]
    scap = cap[si]
    bx, by, bz = B[b, 0], B[b, 1], B[b, 2]
    right = (W - s4) < s1
    x0 = s0
    y0 = s4 - by if right else s1
    z0 = s2
    out = np.empty((n * 5 + 2, 6), np.int64)
    oc = np.empty(n * 5 + 2)
    m = _split(sp, cap, x0, y0, z0, x0 + bx, y0 + by, z0 + bz, out, oc)
    # above: over the block's top only
    tx, ty = B[b, 3], B[b, 4]
    ty0 = y0 + by - ty if right else y0
    tc = ba[b] if scap >= INF / 2 else min(scap - bp[b], ba[b])
    if z0 + bz < s5 and tc > 0:
        out[m, 0], out[m, 1], out[m, 2] = max(x0, s0), max(ty0, s1), z0 + bz
        out[m, 3], out[m, 4], out[m, 5] = min(x0 + tx, s3), min(ty0 + ty, s4), s5
        oc[m] = tc
        m += 1
    osp, ocap = _clean(out[:m], oc[:m], m, mind)
    osp, ocap = _join(osp, ocap, osp.shape[0])
    osp, ocap = _clean(osp, ocap, osp.shape[0], mind)
    return osp, ocap, x0, y0, z0, right


@nb.njit(cache=True)
def _mind(rem, tmin):
    m = 1 << 40
    for t in range(rem.shape[0]):
        if rem[t] > 0 and tmin[t] < m:
            m = tmin[t]
    return m


@nb.njit(cache=True)
def _greedy(sp, cap, rem, wleft, B, bw, bp, ba, W, tmin, vol0, depth0, maxp, mode):
    """Finish greedily: largest fitting block into the chosen space. Returns the volume packed, the depth used
    and the placements (x0, y0, z0, block, right)."""
    rem = rem.copy()
    placed = np.empty((maxp, 5), np.int64)
    k = 0
    vol, depth = vol0, depth0
    buf = np.empty(1, np.int64)
    while sp.shape[0] > 0 and k < maxp:
        si = _pick(sp, sp.shape[0], W, mode)
        got = _fits(B, bw, bp, rem, sp[si], cap[si], wleft, 1, buf)
        if got == 0:
            keep = np.ones(sp.shape[0], np.bool_)
            keep[si] = False
            sp, cap = sp[keep], cap[keep]
            continue
        b = buf[0]
        rem[B[b, 5]] -= B[b, 6]
        if B[b, 7] >= 0:
            rem[B[b, 7]] -= B[b, 8]
        wleft -= bw[b]
        vol += B[b, 9]
        sp2, cap2, x0, y0, z0, right = _place(sp, cap, si, b, B, bp, ba, W, _mind(rem, tmin))
        depth = max(depth, x0 + B[b, 0])
        placed[k, 0], placed[k, 1], placed[k, 2], placed[k, 3], placed[k, 4] = x0, y0, z0, b, right
        k += 1
        sp, cap = sp2, cap2
    return vol, depth, placed[:k]


# ------------------------------------------------------------------------------------------------ search
@dataclass
class State:
    sp: np.ndarray
    cap: np.ndarray
    rem: np.ndarray
    wleft: float
    vol: int = 0
    depth: int = 0
    placed: list = field(default_factory=list)          # (x0, y0, z0, block, right)

    def copy(self):
        return State(self.sp, self.cap, self.rem.copy(), self.wleft, self.vol, self.depth, list(self.placed))

    @staticmethod
    def empty(cargo: Cargo, veh: Vehicle):
        return State(np.array([[0, 0, 0, veh.L, veh.W, veh.H]], np.int64), np.array([INF]), cargo.count.copy(),
                     float(min(veh.payload, 1e17)))


class Packer:
    """Fills one vehicle (from a start state) with the blocks given."""

    def __init__(self, cargo: Cargo, veh: Vehicle, blocks: Blocks = None, start: State = None, mode=0):
        self.cargo, self.veh = cargo, veh
        self.bl = blocks if blocks is not None else make_blocks(cargo, veh)
        self.tmin = cargo.dims.min(axis=1).astype(np.int64)
        self.start = start if start is not None else State.empty(cargo, veh)
        self.mode = mode
        self.patience = 1 << 30            # with everything loaded, keep looking for a shorter load up to this width

    def score(self, vol, depth):
        return int(vol) * (self.veh.L + 1) + (self.veh.L - int(depth))      # most volume, then shortest load

    def finish(self, st: State):
        bl = self.bl
        vol, depth, pl = _greedy(st.sp, st.cap, st.rem, st.wleft, bl.B, bl.w, bl.p, bl.a, self.veh.W, self.tmin,
                                 st.vol, st.depth, 4096, self.mode)
        return self.score(vol, depth), int(vol), st.placed + [tuple(int(v) for v in r) for r in pl]

    def step(self, st: State, si, b):
        bl = self.bl
        B = bl.B
        rem = st.rem.copy()
        rem[B[b, 5]] -= B[b, 6]
        if B[b, 7] >= 0:
            rem[B[b, 7]] -= B[b, 8]
        sp, cap, x0, y0, z0, right = _place(st.sp, st.cap, si, b, B, bl.p, bl.a, self.veh.W, _mind(rem, self.tmin))
        return State(sp, cap, rem, st.wleft - bl.w[b], st.vol + int(B[b, 9]), max(st.depth, int(x0 + B[b, 0])),
                     st.placed + [(int(x0), int(y0), int(z0), int(b), int(right))])

    def candidates(self, st: State, K):
        if not len(st.sp):
            return -1, np.zeros(0, np.int64)
        si = _pick(st.sp, len(st.sp), self.veh.W, self.mode)
        out = np.empty(K, np.int64)
        k = _fits(self.bl.B, self.bl.w, self.bl.p, st.rem, st.sp[si], st.cap[si], st.wleft, K, out)
        return si, out[:k]

    @staticmethod
    def drop(st: State, si):
        keep = np.ones(len(st.sp), bool)
        keep[si] = False
        return State(st.sp[keep], st.cap[keep], st.rem, st.wleft, st.vol, st.depth, st.placed)

    def pilot(self, K, deadline):
        st = self.start.copy()
        best = self.finish(st)
        while len(st.sp):
            k = K if time.time() < deadline else 1
            si, cand = self.candidates(st, k)
            if not len(cand):
                st = self.drop(st, si)
                continue
            if len(cand) == 1:
                st = self.step(st, si, cand[0])
                continue
            scores = []
            for b in cand:
                r = self.finish(self.step(st, si, b))
                scores.append(r[0])
                if r[0] > best[0]:
                    best = r
            st = self.step(st, si, cand[int(np.argmax(scores))])
        r = (self.score(st.vol, st.depth), st.vol, st.placed)
        return r if r[0] > best[0] else best

    def solve(self, time_limit=5.0, widths=WIDTHS):
        """Best placements found within the time limit: (volume, placements, info)."""
        t0 = time.time()
        deadline = t0 + time_limit
        best = self.finish(self.start.copy())
        mine = np.zeros(self.cargo.T, bool)                # the box types these blocks are made of
        mine[self.bl.B[:, T1]] = True
        everything = int((self.cargo.dims.prod(axis=1) * self.start.rem * mine).sum())
        tried = [1]
        for K in widths[1:]:
            if time.time() >= deadline:
                break
            r = self.pilot(K, deadline)
            tried.append(K)
            improved = r[0] > best[0]
            if improved:
                best = r
            if best[1] - self.start.vol >= everything and not improved and K >= self.patience:
                break                                     # everything is loaded and the load got no shorter
        return best[1] - self.start.vol, best[2][len(self.start.placed):], {"widths": tried,
                                                                            "seconds": time.time() - t0}

    def replay(self, placed):
        """The state after the given placements, and the free spaces set aside on the way (no block fitted them
        then; a later group of boxes may still use them)."""
        st = self.start.copy()
        parked = []
        for (x0, y0, z0, b, right) in placed:
            while True:
                si, cand = self.candidates(st, 1)
                if len(cand):
                    break
                parked.append((st.sp[si].copy(), st.cap[si]))
                st = self.drop(st, si)
            st = self.step(st, si, b)
            if tuple(st.placed[-1]) != (x0, y0, z0, b, right):
                raise RuntimeError("replay went a different way")
        sp = np.array([p[0] for p in parked], np.int64).reshape(-1, 6)
        cap = np.array([p[1] for p in parked], float)
        return st, sp, cap


# ------------------------------------------------------------------------------------------------ boxes
def expand(placed, bl: Blocks):
    """Block placements -> one row per box: x0 y0 z0 x1 y1 z1 type."""
    out = []

    def simple(si, x, y, z):
        t, l, w, h, nx, ny, nz = (int(v) for v in bl.simple[si])
        for i in range(nx):
            for j in range(ny):
                for k in range(nz):
                    out.append((x + i * l, y + j * w, z + k * h, x + (i + 1) * l, y + (j + 1) * w, z + (k + 1) * h, t))

    for (x0, y0, z0, b, right) in placed:
        kd, (pa, pb) = int(bl.kind[b]), bl.parts[b]
        if kd == 0:
            simple(int(pa), x0, y0, z0)
        elif kd == 1:
            simple(int(pa), x0, y0, z0)
            simple(int(pb), x0 + int(bl.simple_dims(pa)[0]), y0, z0)
        elif kd == 2:
            simple(int(pa), x0, y0, z0)
            simple(int(pb), x0, y0 + int(bl.simple_dims(pa)[1]), z0)
        else:
            simple(int(pa), x0, y0, z0)
            _, uy, _ = bl.simple_dims(pb)
            by = int(bl.B[b, 1])
            simple(int(pb), x0, (y0 + by - int(uy)) if right else y0, z0 + int(bl.simple_dims(pa)[2]))
    return np.array(out, np.int64).reshape(-1, 7)


def block_boxes(placed, bl: Blocks):
    """Bounding box of every placed block: x0 y0 z0 x1 y1 z1."""
    return np.array([(x, y, z, x + bl.B[b, 0], y + bl.B[b, 1], z + bl.B[b, 2]) for (x, y, z, b, _) in placed],
                    np.int64).reshape(-1, 6)


def loading_order(boxes, stops=None):
    """An order to load the boxes through the door: a box after everything it rests on and after every box behind
    it in its lane; among those free to go, the last drop-off first, then deepest, then lowest."""
    n = len(boxes)
    if n == 0:
        return np.zeros(0, np.int64)
    b = boxes
    st = np.zeros(n, np.int64) if stops is None else np.asarray(stops)
    before = [[] for _ in range(n)]
    indeg = np.zeros(n, np.int64)
    ox = (np.minimum(b[:, None, 3], b[None, :, 3]) > np.maximum(b[:, None, 0], b[None, :, 0]))
    oy = (np.minimum(b[:, None, 4], b[None, :, 4]) > np.maximum(b[:, None, 1], b[None, :, 1]))
    oz = (np.minimum(b[:, None, 5], b[None, :, 5]) > np.maximum(b[:, None, 2], b[None, :, 2]))
    rest = (b[None, :, 5] == b[:, None, 2]) & ox & oy            # i rests on j
    behind = (b[None, :, 3] <= b[:, None, 0]) & oy & oz          # j is behind i in its lane
    need = rest | behind
    np.fill_diagonal(need, False)
    for i, j in zip(*np.nonzero(need)):
        before[j].append(i)
        indeg[i] += 1

    def key(i):
        return (-int(st[i]), int(b[i, 0]), int(b[i, 2]), int(b[i, 1]), i)

    heap = [key(i) for i in range(n) if indeg[i] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        i = heapq.heappop(heap)[-1]
        order.append(i)
        for k in before[i]:
            indeg[k] -= 1
            if indeg[k] == 0:
                heapq.heappush(heap, key(k))
    if len(order) < n:                                            # should not happen; keep every box
        seen = set(order)
        order += [i for i in range(n) if i not in seen]
    return np.array(order, np.int64)
