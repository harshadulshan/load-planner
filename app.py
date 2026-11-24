"""
Load Planner: fit more boxes into every truck and container, keep weight, stacking and drop-off rules, and hand the
loaders a step-by-step 3D guide.  Run:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from loadplan import demo, planner, report, viewer, workbook
from loadplan.engine import Vehicle

HERE = os.path.dirname(os.path.abspath(__file__))
st.set_page_config(page_title="Load Planner", page_icon="📦", layout="wide")

DARK = getattr(getattr(st.context, "theme", None), "type", None) == "dark"
INK2 = "#c3c2b7" if DARK else "#52514e"
GRID = "#3a3936" if DARK else "#e6e4df"
SERIES = [c[1] if DARK else c[0] for c in viewer.PALETTE]
NEUTRAL = "#8f8e88" if DARK else "#9b9a95"

st.markdown("""<style>
.block-container{padding-top:2.2rem;max-width:1280px}
div[data-testid="stMetricValue"]{font-size:1.55rem}
.lp-sub{color:var(--text-color);opacity:.75;margin-top:-.6rem;margin-bottom:.4rem}
.lp-check{margin:.15rem 0}
</style>""", unsafe_allow_html=True)

ss = st.session_state


def key_of(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.to_csv(index=False).encode() if isinstance(p, pd.DataFrame) else json.dumps(p, default=str).encode())
    return h.hexdigest()[:16]


def show_html(page, height):
    """The 3D page in an iframe (st.iframe where this Streamlit has it)."""
    if hasattr(st, "iframe"):
        st.iframe(page, height=height)
    else:
        components.html(page, height=height)


def stop_names(df):
    out = {}
    if "Customer" in df.columns and "Stop" in df.columns:
        for s, c in zip(df["Stop"], df["Customer"]):
            try:
                s = int(float(s))
            except (TypeError, ValueError):
                continue
            if isinstance(c, str) and c.strip() and s not in out:
                out[s] = c.strip()
    return out


def init():
    if "boxes" not in ss:
        ss.boxes = demo.boxes_table()
        ss.vehicles = demo.vehicles_table()
        ss.source = "demo"
        ss.results = {}
        ss.fleet = {}
        ss.editor_v = 0


init()

# ------------------------------------------------------------------------------------------------ header
st.title("📦 Load Planner")
st.markdown('<div class="lp-sub">Fit more boxes in every truck and container. Weight, stacking, “this side up” and '
            'drop-off order are kept, and the loaders get a step-by-step 3D guide.</div>', unsafe_allow_html=True)

tab_plan, tab_fleet, tab_bench, tab_how = st.tabs(["📦 Plan a load", "🚚 Several vehicles", "🧪 Benchmark",
                                                   "ℹ️ How it works"])


def sidebar():
    with st.sidebar:
        st.markdown("### 📦 Load Planner")
        st.caption("Where every box goes, and in what order to load them.")
        st.divider()
        n = int(pd.to_numeric(ss.boxes.get("Quantity", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        st.markdown(f"✅ Load list: **{n} boxes**" + (" (demo)" if ss.source == "demo" else ""))
        r = ss.results.get(ss.get("last")) if ss.get("last") else None
        if r:
            p = r["plan"]
            st.markdown(f"{'✅' if p.left.sum() == 0 else '⚠️'} Plan: **{int(p.loaded.sum())} of "
                        f"{int(p.cargo.count.sum())}** in the {p.vehicle.name.lower()}")
            st.markdown(f"🕒 Planned in {p.seconds:.1f} s")
        if ss.fleet.get("plans"):
            st.markdown(f"🚚 Trips: **{len(ss.fleet['plans'])}**")
        st.divider()
        st.caption("Rules kept: inside the vehicle, every box fully supported, this side up, the weight each box "
                   "can carry, the payload, and drop-off order. Demo names are fictional.")


# ------------------------------------------------------------------------------------------------ inputs
def data_panel():
    a, b = st.columns([1.1, 1])
    with a:
        src = st.radio("Load list", ["Demo: Velora Home Appliances (fictional)", "My own file"], horizontal=True,
                       index=0 if ss.source == "demo" else 1, key="src")
        if src.startswith("Demo") and ss.source != "demo":
            ss.boxes, ss.vehicles, ss.source = demo.boxes_table(), demo.vehicles_table(), "demo"
            ss.editor_v += 1
        if not src.startswith("Demo"):
            up = st.file_uploader("Excel (.xlsx) from the template, or a .csv of the Boxes sheet",
                                  type=["xlsx", "csv"], key="upload")
            if up is not None and ss.source != up.name + str(up.size):
                try:
                    bx, vh = workbook.read(up)
                    demo.cargo_from_table(bx)                      # check it now, with a plain message
                    if vh is not None:
                        demo.vehicles_from_table(vh)
                except ValueError as e:
                    st.error(f"That file could not be used: {e}")
                else:
                    cols = [c for c in workbook.BOX_COLS if c in bx.columns]
                    ss.boxes = bx[cols + [c for c in bx.columns if c not in cols]].reset_index(drop=True)
                    if vh is not None:
                        ss.vehicles = vh
                    ss.source = up.name + str(up.size)
                    ss.editor_v += 1
                    st.success(f"Read {len(bx)} rows from {up.name}.")
        st.download_button("⬇ Excel template (with the demo filled in)", workbook.template_bytes(),
                           "load-planner-template.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with b:
        names = [str(v) for v in ss.vehicles["Vehicle"]] + ["Custom size"]
        default = names.index("Medium truck (6.1 m)") if "Medium truck (6.1 m)" in names else 0
        vname = st.selectbox("Vehicle", names, index=default, key="vname")
        if vname == "Custom size":
            r = {"Length (cm)": 600, "Width (cm)": 240, "Height (cm)": 240, "Payload (kg)": 7000.0}
        else:
            r = ss.vehicles[ss.vehicles["Vehicle"].astype(str) == vname].iloc[0].to_dict()
        c1, c2, c3, c4 = st.columns(4)
        L = c1.number_input("Inside length (cm)", 50, 3000, int(r["Length (cm)"]), key=f"L_{vname}")
        W = c2.number_input("Width (cm)", 50, 400, int(r["Width (cm)"]), key=f"W_{vname}")
        H = c3.number_input("Height (cm)", 50, 400, int(r["Height (cm)"]), key=f"H_{vname}")
        pay = c4.number_input("Payload (kg)", 0.0, 100000.0, float(r.get("Payload (kg)", 0) or 0), step=100.0,
                              key=f"P_{vname}", help="0 means no weight limit")
        secs = st.slider("Thinking time (seconds)", 1, 15, 3, key="secs",
                         help="More time lets the planner try more ways; small loads finish sooner.")
    veh = Vehicle(vname, int(L), int(W), int(H), float(pay) if pay > 0 else np.inf)
    with st.expander(f"Boxes to load ({int(pd.to_numeric(ss.boxes['Quantity'], errors='coerce').fillna(0).sum())} "
                     "boxes). Edit here or upload your own list.", expanded=False):
        edited = st.data_editor(
            ss.boxes, num_rows="dynamic", key=f"editor_{ss.editor_v}",
            column_config={
                "Max kg on top": st.column_config.TextColumn(help="A number, 'nothing' (fragile) or 'no limit'"),
                "This side up": st.column_config.SelectboxColumn(options=["yes", "no"]),
                "Stop": st.column_config.NumberColumn(min_value=1, step=1,
                                                      help="1 is unloaded first (nearest the door)"),
                "Quantity": st.column_config.NumberColumn(min_value=0, step=1)})
    return edited, veh, float(secs)


def run_plan(boxes, veh, secs):
    cargo = demo.cargo_from_table(boxes)
    plan = planner.plan_load(cargo, veh, secs)
    hand = planner.by_hand(cargo, veh)
    return {"plan": plan, "hand": hand, "stops": stop_names(boxes)}


def check_line(ok, text):
    st.markdown(f'<div class="lp-check">{"✅" if ok else "⚠️"} {text}</div>', unsafe_allow_html=True)


def show_balance(plan):
    b = plan.balance()
    if b is None:
        st.caption("Add box weights to see the balance.")
        return
    v = plan.vehicle
    check_line(b["half_rule"], f"{b['front_half'] * 100:.0f}% of the weight in the front half, "
                               f"{b['door_half'] * 100:.0f}% in the door half (guide: at most 60% in either half).")
    side = abs(b["sideways"])
    check_line(b["sideways_ok"], f"Balance point {side:.0f} cm {'right' if b['sideways'] > 0 else 'left'} of the "
                                 f"centre line (our check: within 5% of the width, {0.05 * v.W:.0f} cm).")
    check_line(b["height_ok"], f"Balance point {b['cog'][2]:.0f} cm up, "
                               f"{'below' if b['height_ok'] else 'above'} half the height ({v.H / 2:.0f} cm).")
    st.caption("Guidance from the CTU Code (IMO/ILO/UNECE code of practice for packing cargo transport units). "
               "Check axle loads for your vehicle as well.")


def show_plan(res, boxes_now_key=None):
    plan, hand, sn = res["plan"], res["hand"], res["stops"]
    cargo, v = plan.cargo, plan.vehicle
    total = int(cargo.count.sum())
    got, hand_got = int(plan.loaded.sum()), int(hand.loaded.sum())
    m = st.columns(5)
    m[0].metric("Boxes loaded", f"{got} of {total}", f"{got - hand_got:+d} vs by hand" if got != hand_got else None,
                border=True)
    gain = (plan.fill - hand.fill) * 100
    m[1].metric("Space used", f"{plan.fill * 100:.1f}%", f"{gain:+.1f} points vs by hand" if abs(gain) >= 0.05
                else None, border=True)
    m[2].metric("Weight", f"{plan.weight:,.0f} kg",
                help=f"payload {v.payload:,.0f} kg" if np.isfinite(v.payload) else "no payload limit set",
                border=True)
    m[3].metric("Load reaches", f"{plan.depth / 100:.2f} m", help=f"of {v.L / 100:.2f} m inside length",
                border=True)
    left_hand = total - hand_got
    kw = dict(delta_color="off", border=True, help="A careful loader without a plan: biggest boxes first, each stack "
              "as far back as it goes, smaller boxes on top where the weight rules allow.")
    note = f"{left_hand} need another trip" if left_hand else "all fit"
    try:
        m[4].metric("Loaded by hand", f"{hand_got} of {total}", note, delta_arrow="off", **kw)
    except TypeError:                                   # Streamlit without delta_arrow
        m[4].metric("Loaded by hand", f"{hand_got} of {total}", note, **kw)
    row = st.columns([1, 2.2])
    which = row[0].radio("Show", ["Planner", "By hand"], horizontal=True, key="which", label_visibility="collapsed")
    shown = plan if which == "Planner" else hand
    bal = shown.balance()
    room = v.L - shown.depth
    if bal is not None and not bal["half_rule"] and room > 0 and shown.left.sum() == 0:
        if row[1].toggle("Part load: move it towards the middle of the floor (brace the gap at the front wall)",
                         key="centre", help="More than 60% of the weight is in one half of the length. Moving the "
                         "whole load keeps every box's place relative to the others."):
            ck = f"centred_{which}"
            if ck not in res:
                res[ck] = planner.centred(shown)
            shown = res[ck]
    show_html(viewer.html(shown, height=560, dark=DARK, stop_names=sn, autoplay=True,
                          speed=max(6.0, len(shown.boxes) / 25)), 566)
    st.caption("Drag to turn, scroll to zoom, hover a box for what it is. Space bar plays or pauses; the arrow keys "
               "step one box. Distances are from the front wall and from the left side, seen from the door.")
    a, b = st.columns([1.1, 1])
    with a:
        st.subheader("Balance")
        show_balance(shown)
        gap = shown.info.get("gap_at_front_wall")
        if gap:
            st.caption(f"The load starts {gap} cm from the front wall: block or brace that gap before you drive.")
    with b:
        st.subheader("Not loaded")
        lt = shown.left
        if lt.sum() == 0:
            st.success("Everything fits.")
        else:
            st.dataframe(pd.DataFrame([{"Item": viewer.item_of(cargo.names[t]), "Stop": int(cargo.stop[t]),
                                        "Boxes": int(lt[t])} for t in np.flatnonzero(lt)]),
                         hide_index=True)
            st.caption("Plan these on another vehicle in the “Several vehicles” tab.")
    st.subheader("For the loaders")
    d1, d2, _ = st.columns([1, 1, 2])
    title = "Load plan" + (": Velora Home Appliances (fictional)" if ss.source == "demo" else "")
    k = id(shown)
    if res.get("pdf_for") != k:
        res["pdf"] = report.pdf(shown, title, sn, by_hand=hand if shown is plan else None)
        res["xlsx"] = workbook.manifest_bytes([shown], sn, shown.left, cargo)
        res["pdf_for"] = k
    d1.download_button("⬇ Loading sheet (PDF)", res["pdf"], "load-plan.pdf", "application/pdf", type="primary")
    d2.download_button("⬇ Manifest (Excel)", res["xlsx"], "load-manifest.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with st.expander("Loading steps, box by box"):
        st.dataframe(pd.DataFrame(report.steps_table(shown)), hide_index=True, height=360)


with tab_plan:
    boxes, veh, secs = data_panel()
    k = key_of(boxes, [veh.name, veh.L, veh.W, veh.H, veh.payload, secs])
    go_now = st.button("Plan the load", type="primary", key="plan_btn")
    if go_now or (not ss.results and ss.source == "demo"):
        try:
            with st.spinner(f"Planning… (up to {secs:.0f} s; the very first plan on a computer also prepares the "
                            "engine, about 15 s)"):
                ss.results = {k: run_plan(boxes, veh, secs)}
                ss.last = k
        except ValueError as e:
            st.error(f"The load list has a problem: {e}")
    if ss.get("last") in ss.results:
        if ss.last != k:
            st.info("The boxes or the vehicle changed. Press **Plan the load** to update the plan below.")
        show_plan(ss.results[ss.last])


# ------------------------------------------------------------------------------------------------ fleet
with tab_fleet:
    st.markdown("Too much for one vehicle? List what you have; the planner picks vehicles one after the other: the "
                "cheapest one that takes everything left, otherwise the one that carries most for its cost, and "
                "fills each one as well as it can. The same choice with loading by hand is shown for comparison.")
    vtab = st.data_editor(ss.vehicles, num_rows="dynamic", key=f"veh_{ss.editor_v}",
                          column_config={"Available": st.column_config.NumberColumn(min_value=0, step=1)})
    fsecs = st.slider("Thinking time per vehicle (seconds)", 1, 10, 2, key="fsecs")
    fk = key_of(boxes, vtab, [fsecs])
    if st.button("Plan the trips", type="primary", key="fleet_btn"):
        try:
            cargo = demo.cargo_from_table(boxes)
            vs = demo.vehicles_from_table(vtab)
            with st.spinner("Planning the trips…"):
                plans, left = planner.plan_fleet(cargo, vs, float(fsecs))
                hplans, hleft = planner.plan_fleet(cargo, vs, 0, method="by hand")
            ss.fleet = {"key": fk, "plans": plans, "left": left, "hand": hplans, "hleft": hleft, "cargo": cargo,
                        "stops": stop_names(boxes)}
        except ValueError as e:
            st.error(f"Cannot plan: {e}")
    if ss.fleet.get("plans") is not None:
        f = ss.fleet
        if f["key"] != fk:
            st.info("The boxes or vehicles changed. Press **Plan the trips** to update.")
        plans, hplans = f["plans"], f["hand"]
        cost, hcost = sum(p.vehicle.cost for p in plans), sum(p.vehicle.cost for p in hplans)
        m = st.columns(4)
        m[0].metric("Trips", len(plans), f"{len(plans) - len(hplans):+d} vs by hand" if len(plans) != len(hplans)
                    else None, delta_color="inverse", border=True)
        m[1].metric("Cost", f"{cost:,.0f}", f"{cost - hcost:+,.0f} vs by hand" if cost != hcost else None,
                    delta_color="inverse", border=True)
        m[2].metric("Boxes that fit nowhere", int(f["left"].sum()), border=True,
                    help="Too big or too heavy for every vehicle listed, or not enough vehicles.")
        m[3].metric("By hand", f"{len(hplans)} trip{'s' if len(hplans) != 1 else ''}, cost {hcost:,.0f}",
                    border=True, help="The same way of choosing vehicles, each loaded by hand.")
        st.dataframe(pd.DataFrame([{"Trip": i + 1, "Vehicle": p.vehicle.name, "Boxes": int(p.loaded.sum()),
                                    "Space used": f"{p.fill * 100:.1f}%", "Weight (kg)": round(p.weight),
                                    "Cost": p.vehicle.cost,
                                    "Stops": ", ".join(str(s) for s in sorted(set(p.stops.tolist())))}
                                   for i, p in enumerate(plans)]), hide_index=True)
        if plans:
            t = st.selectbox("Show trip", [f"Trip {i + 1}: {p.vehicle.name}" for i, p in enumerate(plans)],
                             key="trip")
            p = plans[int(t.split(":")[0].split()[1]) - 1]
            show_html(viewer.html(p, height=520, dark=DARK, stop_names=f["stops"], autoplay=True,
                                  speed=max(6.0, len(p.boxes) / 25)), 526)
            st.download_button("⬇ Manifest for all trips (Excel)",
                               workbook.manifest_bytes(plans, f["stops"], f["left"], f["cargo"]),
                               "trips-manifest.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------------------------------------ benchmark
with tab_bench:
    path = os.path.join(HERE, "benchmarks", "results_br.json")
    if not os.path.exists(path):
        st.info("No benchmark results yet. Run `python benchmarks/run_br.py` to make them.")
    else:
        R = json.load(open(path))
        pub = json.load(open(os.path.join(HERE, "benchmarks", "br_published.json")))
        sets = [s for s in pub["sets"] if s in R["sets"]]
        st.markdown(
            "The standard test for container loading: the BR1–BR15 problems (Bischoff & Ratcliff 1995; Davies & "
            "Bischoff 1999), 100 loads each for a 20 ft container, from 3 to 100 box sizes per load. Every box must "
            "be fully supported from below. Each of our plans was checked box by box; the published numbers are "
            "from Table 4 of Gonçalves & Resende (2012).")
        av = R.get("averages", {})
        secs_used = np.mean([R["sets"][s]["planner"]["mean_seconds"] for s in sets])
        c = st.columns(4)
        if "BR1-15" in av:
            c[0].metric("Load Planner, BR1–15", f"{av['BR1-15']['planner']:.2f}%", border=True,
                        help="mean space used over 1,500 loads")
            c[1].metric("Time per load", f"{secs_used:.1f} s", border=True,
                        help=f"limit {R['sets'][sets[0]]['seconds_limit']:.0f} s, on {R['machine']['cpus']} CPU "
                             "cores running two loads at a time")
        best = pub["averages_from_table"]["BRKGA-CLP-S"]["BR1-15"]
        c[2].metric("Best published (232 s per load)", f"{best:.2f}%", border=True, help="BRKGA-CLP-S, 2012")
        if "BR1-15" in av:
            c[3].metric("By hand (careful loader)", f"{av['BR1-15']['by_hand']:.2f}%", border=True)
        fig = go.Figure()
        show = [("FB_TRS", "Fanslau & Bortfeldt 2010 (320 s)"), ("BRKGA-CLP-S", "Gonçalves & Resende 2012 (232 s)"),
                ("BG_PGA", "Gehring & Bortfeldt 2002"), ("MO_GR", "Moura & Oliveira 2005")]
        for i, (m_, name) in enumerate(show):
            fig.add_trace(go.Scatter(x=pub["sets"], y=pub["methods"][m_]["fill"], name=name, mode="lines+markers",
                                     line=dict(color=SERIES[i + 1], width=2), marker=dict(size=7),
                                     hovertemplate="%{x}: %{y:.2f}%<extra>" + name + "</extra>"))
        fig.add_trace(go.Scatter(x=sets, y=[R["sets"][s]["planner"]["mean_fill"] for s in sets],
                                 name="Load Planner (this app)", mode="lines+markers",
                                 line=dict(color=SERIES[0], width=3), marker=dict(size=9),
                                 hovertemplate="%{x}: %{y:.2f}%<extra>Load Planner</extra>"))
        fig.add_trace(go.Scatter(x=sets, y=[R["sets"][s]["by_hand"]["mean_fill"] for s in sets],
                                 name="By hand (careful loader)", mode="lines+markers",
                                 line=dict(color=NEUTRAL, width=2, dash="dot"), marker=dict(size=7),
                                 hovertemplate="%{x}: %{y:.2f}%<extra>By hand</extra>"))
        fig.update_layout(height=470, margin=dict(l=10, r=10, t=70, b=10), paper_bgcolor="rgba(0,0,0,0)",
                          plot_bgcolor="rgba(0,0,0,0)", font=dict(color=INK2),
                          legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0), hovermode="x unified",
                          yaxis=dict(title="Space used (%)", gridcolor=GRID, zeroline=False),
                          xaxis=dict(title="Test set (more box sizes to the right)", gridcolor=GRID))
        st.plotly_chart(fig)
        rows = []
        for s in sets:
            r = R["sets"][s]
            i = pub["sets"].index(s)
            rows.append({"Set": s, "Load Planner (%)": round(r["planner"]["mean_fill"], 2),
                         "No search (%)": round(r["no_search"]["mean_fill"], 2),
                         "By hand (%)": round(r["by_hand"]["mean_fill"], 2),
                         "Fanslau & Bortfeldt (%)": pub["methods"]["FB_TRS"]["fill"][i],
                         "Gonçalves & Resende (%)": pub["methods"]["BRKGA-CLP-S"]["fill"][i],
                         "Seconds": round(r["planner"]["mean_seconds"], 1),
                         "Invalid plans": r["planner"]["invalid"] + r["no_search"]["invalid"] + r["by_hand"]["invalid"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        st.caption("No search: the same engine placing the largest block that fits each time, without trying "
                   "alternatives. The published methods ran on other computers; their times are as reported.")


# ------------------------------------------------------------------------------------------------ how it works
with tab_how:
    st.markdown("""
**What it does.** You list the boxes (size, number, weight, how much they can carry on top, whether they must stay
upright, and which drop-off they are for) and the vehicle. The planner decides where every box goes and in what
order to load them.

**How it decides.**

1. **Blocks.** Boxes of one kind are grouped into blocks: a stack, a row, a layer, or two such blocks side by side
   or one on the other. Placing a whole block at once keeps the load tidy and stable.
2. **Free spaces.** The empty room in the vehicle is kept as a list of the largest empty boxes. Every free space has
   a full floor under it, so every box ends up fully supported.
3. **Look ahead.** For the next free space (nearest the front wall), the planner tries several blocks, finishes the
   rest of the load quickly for each, and keeps the block that leads to the fullest vehicle. With time left it tries
   more blocks each step (1, 2, 4, … up to 1,024).
4. **Weight.** Each box has a limit for what it can carry, spread over its top. The planner tracks how much more
   every free space's floor can take, all the way down, so no box is overloaded and fragile boxes get nothing on
   top. The payload is never exceeded.
5. **Drop-offs.** The last stop is packed first, deepest in. Nothing for an earlier stop may go behind a box for a
   later stop, so at each stop the boxes come out without moving anyone else's.
6. **Loading order.** Boxes are numbered so that each one goes in after what it rests on and after what is behind
   it, last stop first.

**Checked, box by box.** Every plan is checked before you see it in tests: inside the vehicle, no overlaps, full
support, allowed orientations, counts, payload, the weight on every box, and the drop-off order.

**Limits.** Boxes are rectangular and placed square to the walls. Axle loads are not computed (only the balance
point and the 60% guide). Loads are planned as packed volumes; leave room for straps and dunnage if you need it.
""")

sidebar()
