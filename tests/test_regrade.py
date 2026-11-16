"""`regrade` in the watcher: grade a held student again, then on through Gradescope as usual."""
import io, json, contextlib
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check
import fakegs
from fakegs import FakeGS

def cfg_extra(c):
    c['assignments'] = [{'name': 'HW3', 'rubric_file': 'HW3_rubric.yaml', 'solution_file': 'data/solutions/HW3-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00', 'gradescope': {'tex': 10, 'pdf': 20}}]
    c['watch'] = {'source': 'gradescope', 'gradescope': {'course': 1}, 'send_notices': False}
fx.workdir(cfg_extra); fx.assets("HW3")
fx.patch_grader()
from lata import grader as g
base = g.LatexGrader.grade_chunk
trouble = {"fail": {"Kim"}, "stop": False, "calls": []}
def flaky(self, problem_label, *a, student_id=None, **kw):
    trouble["calls"].append((student_id, problem_label))
    if trouble["stop"] and "Kim" in student_id:
        raise KeyboardInterrupt
    if problem_label == "Problem 1" and any(n in student_id for n in trouble["fail"]):
        return []                                   # e.g. the model's JSON was invalid, twice
    return base(self, problem_label, *a, student_id=student_id, **kw)
g.LatexGrader.grade_chunk = flaky
smtp = fx.patch_emailer()
gs = FakeGS(); fakegs.install(gs)
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 12, 0)
gs.tex = {s: (100 + int(s), "2026-09-30 10:00:00") for s in ("1", "3")}
gs.pdf = {s: (200 + int(s), "2026-09-30 10:05:00") for s in ("1", "3")}
cfg = yaml.safe_load(open("config.yaml"))
w = W.Watcher(cfg, "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
state = lambda: json.loads(Path("data/output/HW3_Feedback/watch_state.json").read_text())["students"]
def out(f):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = f()
    return r, buf.getvalue()

check(state()["3"].get("held") and 2030 not in {q for q, _, _ in gs.saves}, "Kim held (Problem 1 failed), not on Gradescope")
_, listing = out(lambda: W.handle_command(w, "held"))
check("regrade <name>" in listing, "`held` offers regrade alongside approve")
check(W.handle_command(w, "regrade Jane") is False, "only held students can be regraded this way")

# A stop in the middle of the regrade: nothing half-done gets released
trouble["fail"] = set(); trouble["stop"] = True
check(W.handle_command(w, "regrade kim") is True, "`regrade kim` queues it and asks for a check now")
w.run_pass()
st = state()["3"]
check(st.get("regrade") and not st.get("graded"), "stopped mid-regrade: still marked for regrade, not recorded as graded")
gs.published = True; gs.sketch_graded |= {"1", "3"}
w2 = W.Watcher(cfg, "config.yaml", GradingOptions()); w2.authenticate()
W.handle_command(w2, "send")
check(sorted(m['To'] for m in smtp.sent if list(m.iter_attachments())) == ["doe1@oregonstate.edu"],
      "a `send` meanwhile releases Jane only -- Kim's old report is not adopted")

# Relaunched: the regrade finishes and Kim goes through like anyone else
trouble["stop"] = False; trouble["calls"].clear()
w2.run_pass()
st = state()["3"]
check(st.get("graded") and not st.get("held") and not st.get("regrade"), "relaunched: regraded, clean, no longer held")
check({l for sid, l in trouble["calls"] if "Kim" in sid} == {"Problem 1", "Problem 2"}, "Kim's problems were sent to the grader")
check(2030 in {q for q, _, _ in gs.saves}, "her grade went onto Gradescope")
W.handle_command(w2, "send")
check("lee3@oregonstate.edu" in [m['To'] for m in smtp.sent if list(m.iter_attachments())], "and `send` releases her")
fx.done()
