"""
Isometric drawing of a load, as seen from the door, from above and from the left side: the projection, the three
visible faces of each box, and a back-to-front drawing order (painter's order) that is right for boxes that do not
overlap.
"""

from __future__ import annotations

import heapq

import numpy as np

S2, S6 = np.sqrt(2.0), np.sqrt(6.0)


def project(x, y, z):
    """Screen position (right, up) of a point; x towards the door, y across (0 = left side), z up."""
    return (np.asarray(x) + np.asarray(y)) / S2, (np.asarray(y) - np.asarray(x) + 2 * np.asarray(z)) / S6


def faces(b):
    """The three faces of a box seen from the door / left / above, as lists of corners, with a shade factor."""
    x0, y0, z0, x1, y1, z1 = (float(v) for v in b[:6])
    return [
        ([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], 0.78),     # door side
        ([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], 0.92),     # left side
        ([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], 1.0),      # top
    ]


def outline(b):
    """Screen polygon of the whole box (its silhouette hexagon)."""
    x0, y0, z0, x1, y1, z1 = (float(v) for v in b[:6])
    pts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)]
    return [tuple(float(v) for v in project(*p)) for p in pts]


def order(boxes, proj=None):
    """Drawing order: a box after every box it hides part of (for any view from the door side, above and the
    left; proj: the screen projection used to tell which boxes overlap on screen, iso.project by default)."""
    proj = proj or project
    n = len(boxes)
    if n == 0:
        return np.zeros(0, np.int64)
    b = np.asarray(boxes, float)[:, :6]
    cx = np.stack([proj(b[:, i], b[:, j], b[:, k])[0] for i, j, k in
                   [(0, 1, 2), (3, 1, 2), (3, 4, 2), (3, 4, 5), (0, 4, 5), (0, 1, 5), (0, 4, 2), (3, 1, 5)]], 1)
    cy = np.stack([proj(b[:, i], b[:, j], b[:, k])[1] for i, j, k in
                   [(0, 1, 2), (3, 1, 2), (3, 4, 2), (3, 4, 5), (0, 4, 5), (0, 1, 5), (0, 4, 2), (3, 1, 5)]], 1)
    lx, hx, ly, hy = cx.min(1), cx.max(1), cy.min(1), cy.max(1)
    overlap = ((np.minimum(hx[:, None], hx[None]) > np.maximum(lx[:, None], lx[None]) + 1e-9)
               & (np.minimum(hy[:, None], hy[None]) > np.maximum(ly[:, None], ly[None]) + 1e-9))
    # i before j (i is further away) if a plane separates them with i on the far side
    far = ((b[:, None, 3] <= b[None, :, 0]) | (b[:, None, 1] >= b[None, :, 4]) | (b[:, None, 5] <= b[None, :, 2]))
    near = ((b[None, :, 3] <= b[:, None, 0]) | (b[None, :, 1] >= b[:, None, 4]) | (b[None, :, 5] <= b[:, None, 2]))
    before = overlap & far & ~near
    np.fill_diagonal(before, False)
    indeg = before.sum(axis=0)
    key = b[:, 0] - b[:, 4] + b[:, 2]                # roughly far to near, for ties and safety
    heap = [(key[i], i) for i in range(n) if indeg[i] == 0]
    heapq.heapify(heap)
    out, seen = [], np.zeros(n, bool)
    while heap:
        _, i = heapq.heappop(heap)
        out.append(i)
        seen[i] = True
        for j in np.flatnonzero(before[i]):
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(heap, (key[j], j))
    if len(out) < n:                                 # a cycle (should not happen): the rest by the rough key
        out += sorted(np.flatnonzero(~seen), key=lambda i: key[i])
    return np.array(out, np.int64)


def bounds(L, W, H):
    """Screen bounding box of the vehicle."""
    pts = [project(x, y, z) for x in (0, L) for y in (0, W) for z in (0, H)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def vehicle_edges(L, W, H):
    """Screen lines of the vehicle frame: (far edges, near edges); far ones go behind the load."""
    c = {(i, j, k): project(i * L, j * W, k * H) for i in (0, 1) for j in (0, 1) for k in (0, 1)}
    edges = [((0, 0, 0), (1, 0, 0)), ((0, 1, 0), (1, 1, 0)), ((0, 0, 1), (1, 0, 1)), ((0, 1, 1), (1, 1, 1)),
             ((0, 0, 0), (0, 1, 0)), ((1, 0, 0), (1, 1, 0)), ((0, 0, 1), (0, 1, 1)), ((1, 0, 1), (1, 1, 1)),
             ((0, 0, 0), (0, 0, 1)), ((1, 0, 0), (1, 0, 1)), ((0, 1, 0), (0, 1, 1)), ((1, 1, 0), (1, 1, 1))]
    # edges touching the far corner (front wall, right side, floor level) are hidden behind a full load
    far = [e for e in edges if (0, 1, 0) in e or e in (((0, 1, 1), (1, 1, 1)), ((0, 0, 1), (0, 1, 1)))]
    near = [e for e in edges if e not in far]
    return [(c[a], c[b]) for a, b in far], [(c[a], c[b]) for a, b in near]
