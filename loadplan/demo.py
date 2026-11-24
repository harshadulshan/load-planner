"""
A made-up home-appliance distributor and the usual vehicles, for trying the planner. Every business name here is
invented. Box sizes and weights are typical for such goods; vehicle sizes are typical inside dimensions, so check
your own vehicle before relying on them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import Cargo, Vehicle

COMPANY = "Velora Home Appliances (fictional)"

STOPS = {1: "Marisett Electronics", 2: "Dunhollow Home Centre", 3: "Brightwater Stores"}

# name, length, width, height (cm), weight (kg), max kg on top (inf: no limit, 0: nothing on top),
# this side up?, quantity for stops 1, 2, 3
PRODUCTS = [
    ("Fridge, 2-door",          70, 68, 172, 62.0, 0.0,    True,  (2, 3, 2)),
    ("Washing machine",         62, 64, 90,  70.0, 75.0,   True,  (4, 2, 2)),
    ('TV 55" carton',           138, 17, 88, 21.0, 0.0,    True,  (4, 3, 4)),
    ("Microwave oven",          54, 44, 34,  14.0, 60.0,   True,  (8, 10, 8)),
    ("Gas cooker, 2-burner",    76, 46, 17,  9.0,  50.0,   True,  (10, 8, 10)),
    ("Rice cooker",             40, 38, 33,  5.5,  40.0,   True,  (12, 10, 14)),
    ("Pedestal fan",            72, 22, 46,  6.5,  30.0,   False, (12, 14, 12)),
    ("Blender",                 30, 26, 42,  4.0,  25.0,   True,  (16, 14, 14)),
]

# name, inside length, width, height (cm), payload (kg), cost per trip, available
VEHICLES = [
    ("Van (3.3 m)",                330, 170, 180, 1200,  60,  4),
    ("Small truck (4.3 m)",        430, 205, 200, 3000,  95,  3),
    ("Medium truck (6.1 m)",       610, 240, 240, 7000,  140, 2),
    ("20 ft container",            589, 235, 239, 28000, 900, 2),
    ("40 ft container",            1203, 235, 239, 26500, 1500, 2),
    ("40 ft high cube container",  1203, 235, 269, 26400, 1600, 2),
]


def boxes_table() -> pd.DataFrame:
    """The demo load as the table the app edits: one row per product and stop."""
    rows = []
    for name, l, w, h, kg, bear, up, qty in PRODUCTS:
        for stop, q in enumerate(qty, 1):
            if q:
                rows.append({"Item": name, "Length (cm)": l, "Width (cm)": w, "Height (cm)": h, "Quantity": q,
                             "Weight (kg)": kg, "Max kg on top": ("nothing" if bear == 0 else
                                                                  ("no limit" if np.isinf(bear) else f"{bear:g}")),
                             "This side up": "yes" if up else "no", "Stop": stop,
                             "Customer": STOPS[stop]})
    return pd.DataFrame(rows)


def vehicles_table() -> pd.DataFrame:
    return pd.DataFrame([{"Vehicle": n, "Length (cm)": L, "Width (cm)": W, "Height (cm)": H, "Payload (kg)": p,
                          "Cost per trip": c, "Available": a} for n, L, W, H, p, c, a in VEHICLES])


def vehicle(name) -> Vehicle:
    for n, L, W, H, p, c, a in VEHICLES:
        if n == name:
            return Vehicle(n, L, W, H, p, c, a)
    raise KeyError(name)


def _bear(v):
    s = str(v).strip().lower()
    if s in ("nothing", "none", "fragile", "0", "0.0"):
        return 0.0
    if s in ("", "no limit", "any", "inf", "nan", "unlimited"):
        return np.inf
    return float(s)


def _yes(v):
    return str(v).strip().lower() in ("yes", "y", "true", "1", "x")


def cargo_from_table(df: pd.DataFrame) -> Cargo:
    """Cargo from the boxes table (rows with a quantity above zero). Raises ValueError with a plain message when a
    row is not usable."""
    need = ["Item", "Length (cm)", "Width (cm)", "Height (cm)", "Quantity"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError("missing column(s): " + ", ".join(miss))
    dims, up, cnt, kg, bear, stop, names = [], [], [], [], [], [], []
    for i, r in df.reset_index(drop=True).iterrows():
        try:
            q = int(float(r["Quantity"]))
            if q <= 0:
                continue
            d = [int(round(float(r[c]))) for c in ("Length (cm)", "Width (cm)", "Height (cm)")]
        except (TypeError, ValueError):
            raise ValueError(f"row {i + 1} ({r.get('Item', '')}): sizes and quantity must be numbers") from None
        if min(d) <= 0:
            raise ValueError(f"row {i + 1} ({r['Item']}): sizes must be above zero")
        side_up = _yes(r.get("This side up", "no"))
        try:
            b = _bear(r.get("Max kg on top", "no limit"))
            w = float(r.get("Weight (kg)", 0) or 0)
            s = int(float(r.get("Stop", 1) or 1))
        except (TypeError, ValueError):
            raise ValueError(f"row {i + 1} ({r['Item']}): weight, max kg on top and stop must be numbers "
                             "(or 'nothing' / 'no limit')") from None
        if w < 0 or b < 0 or s < 1:
            raise ValueError(f"row {i + 1} ({r['Item']}): weight and max kg on top cannot be negative; stops start at 1")
        dims.append(d)
        up.append([False, False, True] if side_up else [True, True, True])
        cnt.append(q)
        kg.append(w)
        bear.append(b)
        stop.append(s)
        label = str(r["Item"])
        if "Customer" in df.columns and str(r.get("Customer", "")).strip() not in ("", "nan"):
            label += f" → {r['Customer']}"
        names.append(label)
    if not dims:
        raise ValueError("no boxes to load (every quantity is zero)")
    return Cargo(np.array(dims), np.array(up), np.array(cnt), np.array(kg), np.array(bear), np.array(stop), names)


def vehicles_from_table(df: pd.DataFrame) -> list:
    out = []
    for i, r in df.reset_index(drop=True).iterrows():
        try:
            L, W, H = (int(round(float(r[c]))) for c in ("Length (cm)", "Width (cm)", "Height (cm)"))
            p = float(r.get("Payload (kg)", np.inf) or np.inf)
            c = float(r.get("Cost per trip", 1) or 1)
            a = int(float(r.get("Available", 1) or 0))
        except (TypeError, ValueError):
            raise ValueError(f"vehicle row {i + 1}: sizes, payload, cost and number must be numbers") from None
        if min(L, W, H) <= 0:
            raise ValueError(f"vehicle row {i + 1}: sizes must be above zero")
        if a > 0:
            out.append(Vehicle(str(r["Vehicle"]), L, W, H, p, c, a))
    if not out:
        raise ValueError("no vehicles available")
    return out
