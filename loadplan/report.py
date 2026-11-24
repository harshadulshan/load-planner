"""
The loading sheet as a PDF: a cover page with the numbers and a picture of the whole load, then the loading steps in
groups, each with a picture (boxes already in the vehicle in grey, this group in colour and numbered) and a table
of where every box goes and what it rests on.
"""

from __future__ import annotations

import datetime as dt
import io
import os

import numpy as np
from reportlab.graphics.shapes import Drawing, Line, Polygon, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from . import iso
from .viewer import PALETTE, item_of

INK, INK2, MUTED, LINE, ACCENT = "#0b0b0b", "#52514e", "#8a8984", "#dcdad4", "#2a78d6"
DONE_FILL = "#d9d7d1"

_FONTS = None


def _fonts():
    """A Unicode font if the computer has one (names may hold any language); otherwise Helvetica."""
    global _FONTS
    if _FONTS:
        return _FONTS
    pairs = [("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
             ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
             ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
             ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf")]
    for reg, bold in pairs:
        if os.path.exists(reg) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("LP", reg))
                pdfmetrics.registerFont(TTFont("LP-Bold", bold))
                _FONTS = ("LP", "LP-Bold", True)
                return _FONTS
            except Exception:                                    # noqa: BLE001 - try the next font
                continue
    _FONTS = ("Helvetica", "Helvetica-Bold", False)
    return _FONTS


def _txt(s):
    s = str(s)
    if _fonts()[2]:
        return s
    return s.replace("→", "->").replace("×", "x").replace("·", "-").encode("latin-1", "replace").decode("latin-1")


def _esc(s):
    return _txt(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _shade(hexcol, f):
    c = colors.HexColor(hexcol)
    return colors.Color(c.red * f, c.green * f, c.blue * f)


def picture(plan, width, height, upto=None, show=None, numbers=None, col=None):
    """Isometric drawing of the boxes [0, upto): those in `show` in colour (with step numbers), the rest grey."""
    b = plan.boxes
    n = len(b) if upto is None else upto
    L, W, H = plan.vehicle.L, plan.vehicle.W, plan.vehicle.H
    x0, y0, x1, y1 = iso.bounds(L, W, H)
    s = min((width - 8) / (x1 - x0), (height - 8) / (y1 - y0))
    ox, oy = (width - s * (x1 - x0)) / 2 - s * x0, (height - s * (y1 - y0)) / 2 - s * y0
    d = Drawing(width, height)

    def pt(p):
        return ox + s * p[0], oy + s * p[1]

    far, near = iso.vehicle_edges(L, W, H)
    for a, c in far:
        d.add(Line(*pt(a), *pt(c), strokeColor=colors.HexColor(LINE), strokeWidth=0.6))
    fl = [pt(iso.project(*q)) for q in [(0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0)]]
    d.add(Polygon([v for q in fl for v in q], fillColor=colors.HexColor("#f3f2ef"), strokeColor=None))
    show = set(range(n)) if show is None else set(show)
    font, bold, _ = _fonts()
    labels = []
    for i in iso.order(b[:n]):
        fill = (col[i] if col is not None else ACCENT) if i in show else DONE_FILL
        for corners, f in iso.faces(b[i]):
            q = [pt(iso.project(*c)) for c in corners]
            d.add(Polygon([v for p in q for v in p], fillColor=_shade(fill, f),
                          strokeColor=colors.white if i in show else colors.HexColor("#b9b7b0"), strokeWidth=0.35))
        if numbers is not None and i in show:
            bx = b[i]
            c = pt(iso.project((bx[0] + bx[3]) / 2, (bx[1] + bx[4]) / 2, bx[5]))
            labels.append((c, str(numbers[i])))
    for a, c in near:
        d.add(Line(*pt(a), *pt(c), strokeColor=colors.HexColor(INK2), strokeWidth=0.7))
    for (cx, cy), t in labels:                                   # numbers last, so nothing covers them
        d.add(String(cx, cy - 2.5, t, fontName=bold, fontSize=6.5, fillColor=colors.white, textAnchor="middle"))
    dp = pt(iso.project(L, 0, 0))
    d.add(String(dp[0] + 5, dp[1] - 3, "DOOR", fontName=bold, fontSize=7, fillColor=colors.HexColor(INK2)))
    fp = pt(iso.project(0, W / 2, H))
    d.add(String(fp[0] - 4, fp[1] + 6, _txt("FRONT WALL"), fontName=bold, fontSize=7,
                 fillColor=colors.HexColor(INK2), textAnchor="end"))
    return d


def steps_table(plan):
    """One row per box in loading order: step, item, stop, where it goes, how it stands, what it rests on."""
    b = plan.boxes
    cg = plan.cargo
    n = len(b)
    rows = []
    ox = (np.minimum(b[:, None, 3], b[None, :, 3]) > np.maximum(b[:, None, 0], b[None, :, 0]))
    oy = (np.minimum(b[:, None, 4], b[None, :, 4]) > np.maximum(b[:, None, 1], b[None, :, 1]))
    rest = (b[None, :, 5] == b[:, None, 2]) & ox & oy if n else np.zeros((0, 0), bool)
    for i in range(n):
        t = int(b[i, 6])
        lab = cg.dims[t]
        h = b[i, 5] - b[i, 2]
        on = "floor" if b[i, 2] == 0 else ", ".join(str(j + 1) for j in np.flatnonzero(rest[i]))
        how = "upright" if h == lab[2] else "on its side"
        if h == lab[2] and (b[i, 3] - b[i, 0]) != lab[0]:
            how = "upright, turned"
        rows.append({"Step": i + 1, "Item": item_of(cg.names[t]), "Stop": int(cg.stop[t]),
                     "From front wall (cm)": int(b[i, 0]), "From left (cm)": int(b[i, 1]),
                     "Height off floor (cm)": int(b[i, 2]), "Size placed (cm)": f"{b[i, 3] - b[i, 0]} × "
                     f"{b[i, 4] - b[i, 1]} × {h}", "Stands": how, "Rests on": on,
                     "Weight (kg)": float(cg.weight[t])})
    return rows


def colours(plan, by="stop"):
    """Fill colour of every box: by drop-off stop (fixed order) or by item."""
    b = plan.boxes
    cg = plan.cargo
    if by == "stop":
        keys = [int(cg.stop[t]) for t in b[:, 6]]
    else:
        keys = [item_of(cg.names[t]) for t in b[:, 6]]
    order = list(dict.fromkeys(sorted(set(keys)) if by == "stop" else keys))
    pal = {k: PALETTE[i][0] if i < 8 else "#9b9a95" for i, k in enumerate(order)}
    return [pal[k] for k in keys], [(k, pal[k]) for k in order]


def pdf(plan, title="Load plan", stop_names=None, group=24, by_hand=None) -> bytes:
    """The loading sheet. group: boxes per page of steps."""
    font, bold, _ = _fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=11 * mm,
                            bottomMargin=11 * mm, title=_txt(title), author="Load Planner")
    st = {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=17, leading=21, textColor=colors.HexColor(INK)),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12, leading=15, textColor=colors.HexColor(INK)),
        "p": ParagraphStyle("p", fontName=font, fontSize=9, leading=12, textColor=colors.HexColor(INK2)),
        "small": ParagraphStyle("s", fontName=font, fontSize=7.5, leading=9.5, textColor=colors.HexColor(INK2)),
        "kpi": ParagraphStyle("k", fontName=bold, fontSize=15, leading=18, textColor=colors.HexColor(INK)),
    }
    v, cg = plan.vehicle, plan.cargo
    col, legend = colours(plan, "stop" if len(set(cg.stop[cg.count > 0])) > 1 else "item")
    sn = stop_names or {}
    total = int(cg.count.sum())
    W_ = doc.width
    story = [Paragraph(_esc(title), st["h1"]),
             Paragraph(_esc(f"{v.name}: inside {v.L} × {v.W} × {v.H} cm" +
                            (f", payload {v.payload:,.0f} kg" if np.isfinite(v.payload) else "") +
                            f" · planned {dt.date.today().isoformat()}"), st["p"]), Spacer(1, 4 * mm)]
    bal = plan.balance()
    kp = [("Boxes loaded", f"{int(plan.loaded.sum())} of {total}"), ("Space used", f"{plan.fill * 100:.1f}%"),
          ("Weight" + (f" (payload {v.payload:,.0f} kg)" if np.isfinite(v.payload) else ""), f"{plan.weight:,.0f} kg"),
          ("Load reaches", f"{plan.depth / 100:.2f} m of {v.L / 100:.2f} m")]
    if by_hand is not None:
        kp.append(("Loaded by hand", f"{int(by_hand.loaded.sum())} of {total}"))
    cells = [[Paragraph(_esc(a), st["small"]) for a, _ in kp], [Paragraph(_esc(b), st["kpi"]) for _, b in kp]]
    t = Table(cells, colWidths=[W_ / len(kp)] * len(kp))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(LINE)),
                           ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(LINE)),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f6f3")),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [t, Spacer(1, 3 * mm)]
    left = []
    pic = picture(plan, W_ * 0.64, 118 * mm, col=col)
    lg = [[Paragraph(f'<font color="{c}">■</font> ' + _esc(f"Stop {k}" + (f" · {sn[k]}" if k in sn else "")
                                                          if isinstance(k, int) else k), st["p"])]
          for k, c in legend]
    if bal:
        lines = [f"Balance point {abs(bal['from_middle']) / 100:.2f} m from the middle towards the "
                 f"{'front wall' if bal['from_middle'] < 0 else 'door'}, {abs(bal['sideways']):.0f} cm "
                 f"{'right' if bal['sideways'] > 0 else 'left'} of the centre line.",
                 f"Weight in the front half {bal['front_half'] * 100:.0f}%, door half {bal['door_half'] * 100:.0f}%"
                 f" ({'within' if bal['half_rule'] else 'over'} the 60% guide).",
                 "Balance point " + ("below" if bal["height_ok"] else "above") + " half the height."]
        lg += [[Spacer(1, 2 * mm)]] + [[Paragraph(_esc(x), st["small"])] for x in lines]
    gap = plan.info.get("gap_at_front_wall")
    if gap:
        lg += [[Spacer(1, 2 * mm)], [Paragraph(_esc(f"Leave {gap} cm free at the front wall and block or brace "
                                                    "that gap (the load is moved towards the middle to balance it)."),
                                               st["p"])]]
    lt = plan.left
    if lt.sum():
        lg += [[Spacer(1, 2 * mm)], [Paragraph("<b>Not loaded</b>", st["p"])]]
        lg += [[Paragraph(_esc(f"{int(lt[t_])} × {cg.names[t_]}"), st["small"])] for t_ in np.flatnonzero(lt)[:18]]
    side = Table(lg, colWidths=[W_ * 0.34])
    side.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    left.append(Table([[pic, side]], colWidths=[W_ * 0.65, W_ * 0.35],
                      style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += left
    story.append(Paragraph(_esc("Load the boxes in step order: each page shows the boxes already in the vehicle in "
                                "grey and the next ones in colour with their step numbers. Distances are measured "
                                "from the front wall (the wall opposite the door) and from the left side as you "
                                "stand at the door looking in."), st["small"]))
    rows = steps_table(plan)
    n = len(rows)
    for a in range(0, n, group):
        z = min(n, a + group)
        story.append(PageBreak())
        story.append(Paragraph(_esc(f"Steps {a + 1}–{z} of {n}"), st["h2"]))
        story.append(Spacer(1, 2 * mm))
        pic = picture(plan, W_ * 0.47, 150 * mm, upto=z, show=range(a, z), numbers={i: i + 1 for i in range(a, z)},
                      col=col)
        head = ["Step", "Item", "Stop", "From front", "From left", "Height", "Stands", "Rests on"]
        data = [head] + [[Paragraph(f'<font color="{col[r["Step"] - 1]}">■</font> {r["Step"]}', st["small"]),
                          Paragraph(_esc(r["Item"]), st["small"]), r["Stop"],
                          f"{r['From front wall (cm)'] / 100:.2f} m", f"{r['From left (cm)']} cm",
                          f"{r['Height off floor (cm)']} cm", Paragraph(_esc(r["Stands"]), st["small"]),
                          Paragraph(_esc(r["Rests on"]), st["small"])]
                         for r in rows[a:z]]
        cw = [13 * mm, 32 * mm, 9 * mm, 18 * mm, 16 * mm, 13 * mm, 22 * mm, 19 * mm]
        tb = Table(data, colWidths=cw, repeatRows=1)
        tb.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), bold, 7.5), ("FONT", (0, 1), (-1, -1), font, 7.5),
                                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
                                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(INK2)),
                                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(LINE)),
                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
        story.append(KeepTogether(Table([[pic, tb]], colWidths=[W_ * 0.48, W_ * 0.52],
                                        style=[("VALIGN", (0, 0), (-1, -1), "TOP"),
                                               ("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    doc.build(story)
    return buf.getvalue()
