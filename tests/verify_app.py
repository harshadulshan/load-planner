"""
App checks with Streamlit's test runner: the demo plans itself on first visit, the numbers on screen match the
planner, switching vehicles, the by-hand view, the part-load option, several vehicles, a bad load list and the
benchmark tab, all without errors.

    python tests/verify_app.py        # about 1 minute
"""

from __future__ import annotations

import os
import sys

ROOT = os.environ.get("LP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

passed, failed = 0, []


def ok(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {detail}")


def metric(at, label):
    for m in at.metric:
        if m.label == label:
            return m.value
    return None


def no_errors(at, name):
    ok(f"{name}: no exceptions", len(at.exception) == 0, [e.value for e in at.exception][:1])


at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=180)
at.run()
no_errors(at, "first visit")
ok("the demo is planned on first visit", metric(at, "Boxes loaded") == "198 of 198", metric(at, "Boxes loaded"))
ok("by-hand comparison shown", (metric(at, "Loaded by hand") or "").endswith("of 198"))
plan = at.session_state["results"][at.session_state["last"]]["plan"]
ok("space used on screen matches the plan", metric(at, "Space used") == f"{plan.fill * 100:.1f}%")
frames = [getattr(h.proto, "srcdoc", "") or getattr(h.proto, "html", "") for h in at.get("iframe")]
ok("the 3D view is on the page with every box", any("webgl" in f and f'"boxes":[' in f and
                                                    f.count("],[") >= len(plan.boxes) - 1 for f in frames))
ok("download buttons for the loaders", len(at.get("download_button")) >= 3)

at.radio(key="which").set_value("By hand").run()
no_errors(at, "by-hand view")

at.selectbox(key="vname").set_value("40 ft container").run()
ok("a changed vehicle asks for a new plan", any("changed" in i.value for i in at.info))
at.button(key="plan_btn").click().run()
no_errors(at, "40 ft container")
ok("40 ft container: everything fits", metric(at, "Boxes loaded") == "198 of 198", metric(at, "Boxes loaded"))
at.radio(key="which").set_value("Planner").run()
tg = [t for t in at.toggle if t.key == "centre"]
ok("part load: the move-to-the-middle option is offered", len(tg) == 1)
if tg:
    tg[0].set_value(True).run()
    no_errors(at, "part load moved")
    ok("part load: the gap to brace is stated", any("brace" in c.value for c in at.caption))
    ok("part load: the 60% check passes after the move",
       any("✅" in m.value and "front half" in m.value for m in at.markdown))

at.selectbox(key="vname").set_value("Van (3.3 m)").run()
at.button(key="plan_btn").click().run()
no_errors(at, "van")
ok("van: not everything fits and the rest is listed",
   (metric(at, "Boxes loaded") or "").split(" of ")[0] != "198" and len(at.dataframe) >= 1)

at.button(key="fleet_btn").click().run()
no_errors(at, "several vehicles")
ok("several vehicles: trips and cost shown", metric(at, "Trips") is not None and metric(at, "Cost") is not None)
f = at.session_state["fleet"]
ok("several vehicles: every box planned once or reported",
   bool(((sum(p.loaded for p in f["plans"]) + f["left"]) == f["cargo"].count).all()))

bad = at.session_state["boxes"].copy().astype(object)
bad.loc[0, "Length (cm)"] = "long"
at.session_state["boxes"] = bad
at.session_state["editor_v"] = at.session_state["editor_v"] + 1
at.run()
at.button(key="plan_btn").click().run()
no_errors(at, "bad load list")
ok("bad load list: a plain message", any("numbers" in e.value for e in at.error), [e.value for e in at.error])

at2 = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=180)
at2.run()
txt = " ".join(m.value for m in at2.markdown)
has_bench = os.path.exists(os.path.join(ROOT, "benchmarks", "results_br.json"))
ok("benchmark tab: results or a note on how to make them",
   ("BR1–BR15" in txt) if has_bench else any("run_br.py" in i.value for i in at2.info))
ok("how-it-works tab explains the rules", "Drop-offs" in txt and "Weight" in txt)
no_errors(at2, "second session")
s1 = pd.DataFrame(at.session_state["boxes"])
ok("sessions do not share their load lists", s1.loc[0, "Length (cm)"] == "long" and
   str(at2.session_state["boxes"].loc[0, "Length (cm)"]) != "long")

print(f"\n{passed} passed, {len(failed)} failed")
if failed:
    print("FAILED: " + "; ".join(failed))
sys.exit(1 if failed else 0)
