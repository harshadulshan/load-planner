"""
Excel in and out: the template to fill in (boxes and vehicles), reading it back, and the manifest of a plan (summary,
loading steps per vehicle, what was not loaded).
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import demo
from .report import steps_table
from .viewer import item_of

BOX_COLS = ["Item", "Length (cm)", "Width (cm)", "Height (cm)", "Quantity", "Weight (kg)", "Max kg on top",
            "This side up", "Stop", "Customer"]
VEH_COLS = ["Vehicle", "Length (cm)", "Width (cm)", "Height (cm)", "Payload (kg)", "Cost per trip", "Available"]

HELP = [
    ("Boxes sheet", ""),
    ("Item", "What the box is. Rows with the same item for different stops are fine."),
    ("Length, Width, Height (cm)", "Outside size of one box. Height is the side that is up when it stands as labelled."),
    ("Quantity", "How many of these boxes."),
    ("Weight (kg)", "Weight of one box. Leave 0 if you do not know; the payload check then cannot help."),
    ("Max kg on top", "Most weight one box can carry spread over its top: a number, 'nothing' (fragile: nothing on "
                      "top) or 'no limit'. A smaller box on top presses on one spot, so it counts for more."),
    ("This side up", "yes: the box must stand as labelled (it may still turn around). no: it may lie on any side."),
    ("Stop", "Drop-off order: 1 is unloaded first (nearest the door), 2 next, and so on. Use 1 for every row if "
             "there is one drop-off."),
    ("Customer", "Optional: who the stop is."),
    ("", ""),
    ("Vehicles sheet", ""),
    ("Length, Width, Height (cm)", "Inside size of the load space. Length runs from the front wall to the door."),
    ("Payload (kg)", "Most weight it may carry."),
    ("Cost per trip", "Used to choose vehicles when there is too much for one."),
    ("Available", "How many of this vehicle you have (0 to leave it out)."),
]

HEAD = PatternFill("solid", fgColor="F3F2EF")


def _style(ws, widths=None):
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = HEAD
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    for i, col in enumerate(ws.columns, 1):
        w = (widths or {}).get(i) or min(46, max(10, max(len(str(c.value or "")) for c in col) + 2))
        ws.column_dimensions[get_column_letter(i)].width = w


def template_bytes(example=True) -> bytes:
    boxes = demo.boxes_table() if example else pd.DataFrame(columns=BOX_COLS)
    boxes = boxes.reindex(columns=BOX_COLS)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        boxes.to_excel(xw, sheet_name="Boxes", index=False)
        demo.vehicles_table().reindex(columns=VEH_COLS).to_excel(xw, sheet_name="Vehicles", index=False)
        pd.DataFrame(HELP, columns=["Column", "What to put in it"]).to_excel(xw, sheet_name="How to fill", index=False)
        _style(xw.sheets["Boxes"])
        _style(xw.sheets["Vehicles"])
        ws = xw.sheets["How to fill"]
        _style(ws, {1: 28, 2: 100})
        for row in ws.iter_rows(min_row=2):
            row[1].alignment = Alignment(wrap_text=True, vertical="top")
            if row[0].value and not row[1].value:
                row[0].font = Font(bold=True)
    return buf.getvalue()


def read(file) -> tuple:
    """(boxes table, vehicles table or None) from an uploaded .xlsx or .csv. Raises ValueError with a plain
    message."""
    name = getattr(file, "name", "") or ""
    try:
        if name.lower().endswith(".csv"):
            return pd.read_csv(file), None
        sheets = pd.read_excel(file, sheet_name=None)
    except Exception as e:                                       # noqa: BLE001 - shown to the user
        raise ValueError(f"could not read the file ({type(e).__name__}). Use the template, saved as .xlsx or .csv.")
    norm = {k.strip().lower(): v for k, v in sheets.items()}
    boxes = norm.get("boxes")
    if boxes is None:
        boxes = next((v for v in sheets.values() if "Quantity" in v.columns), None)
    if boxes is None:
        raise ValueError("no 'Boxes' sheet (or a sheet with a Quantity column) in the file")
    vehicles = norm.get("vehicles")
    boxes = boxes.dropna(how="all")
    if vehicles is not None:
        vehicles = vehicles.dropna(how="all")
    return boxes, vehicles


def manifest_bytes(plans, stop_names=None, left=None, cargo=None) -> bytes:
    """Excel manifest: one summary row per vehicle, the loading steps of each vehicle, and what was not loaded."""
    sn = stop_names or {}
    summ = []
    for k, p in enumerate(plans, 1):
        b = p.balance()
        summ.append({"Trip": k, "Vehicle": p.vehicle.name, "Boxes": int(p.loaded.sum()),
                     "Space used (%)": round(p.fill * 100, 1), "Weight (kg)": round(p.weight, 1),
                     "Payload (kg)": p.vehicle.payload if np.isfinite(p.vehicle.payload) else None,
                     "Load reaches (m)": round(p.depth / 100, 2),
                     "Front / door half (% of weight)": f"{b['front_half'] * 100:.0f} / {b['door_half'] * 100:.0f}"
                     if b else "", "Stops": ", ".join(str(s) for s in sorted(set(p.stops.tolist())))})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(summ).to_excel(xw, sheet_name="Summary", index=False)
        _style(xw.sheets["Summary"])
        for k, p in enumerate(plans, 1):
            rows = steps_table(p)
            for r in rows:
                r["Customer"] = sn.get(r["Stop"], "")
            df = pd.DataFrame(rows)
            name = f"Trip {k} steps" if len(plans) > 1 else "Loading steps"
            df.to_excel(xw, sheet_name=name[:31], index=False)
            _style(xw.sheets[name[:31]])
        if left is not None and cargo is not None and np.sum(left) > 0:
            lt = pd.DataFrame([{"Item": item_of(cargo.names[t]), "Stop": int(cargo.stop[t]),
                                "Customer": sn.get(int(cargo.stop[t]), ""), "Boxes not loaded": int(left[t])}
                               for t in np.flatnonzero(left)])
            lt.to_excel(xw, sheet_name="Not loaded", index=False)
            _style(xw.sheets["Not loaded"])
    return buf.getvalue()
