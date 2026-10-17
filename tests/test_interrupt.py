"""Stopping the watcher mid-batch must not leave students recorded as graded."""
import json
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check

def cfg_extra(c):
    c['assignments'] = [{'name': 'HW3', 'rubric_file': 'HW3_rubric.yaml', 'solution_file': 'data/solutions/HW3-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00'}]
    c['watch'] = {'send_notices': False}
fx.workdir(cfg_extra); fx.assets("HW3")
fx.patch_emailer()
from lata import grader as g
fx.patch_grader()
real = g.LatexGrader.grade_chunk
calls = {"n": 0, "stop_at": None, "fail": set()}
def maybe_stop(self, problem_label, student_text, solution_text, rubric_items, student_id, **kw):
    calls["n"] += 1
    if calls["stop_at"] and calls["n"] == calls["stop_at"]:
        raise KeyboardInterrupt                   # Ctrl-C in the middle of the batch
    if any(n in student_id for n in calls["fail"]):
        return []                                 # e.g. the model server died
    return real(self, problem_label, student_text, solution_text, rubric_items, student_id, **kw)
g.LatexGrader.grade_chunk = maybe_stop

NAMES = {str(i): f"Student {chr(64 + i)}" for i in range(1, 6)}
fx.tex_zip(Path("data/input/inbox/HW3/t.zip"), {i: (NAMES[str(i)], str(i), "2026-09-30 10:00:00") for i in range(1, 6)})
fx.pdf_zip(Path("data/input/inbox/HW3/p.zip"), {10 + i: (NAMES[str(i)], str(i), "2026-09-30 10:05:00") for i in range(1, 6)})
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 12, 0)
cfg = yaml.safe_load(open("config.yaml"))
state = Path("data/output/HW3_Feedback/watch_state.json")
graded = lambda: sorted(k for k, s in json.loads(state.read_text())["students"].items() if s.get("graded"))

calls["stop_at"] = 6          # 2 problems per student: Ctrl-C partway through the third student
w = W.Watcher(cfg, "config.yaml", GradingOptions()); w.authenticate()
keep_going = w.run_pass()
check(keep_going is False, "Ctrl-C stops the watcher")
check(graded() == ["1", "2"], f"only students whose reports were written are recorded as graded: {graded()}")

calls["stop_at"] = None; before = calls["n"]
w = W.Watcher(cfg, "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
check(graded() == ["1", "2", "3", "4", "5"], "relaunched: the other three are graded")
# (Problems finished before the stop are reused by the real grader's result.json resume;
#  this fake grader has no cache, so that is not checked here.)

print("a record left behind by the old bug")
led = json.loads(state.read_text())
for k in ("4", "5"):
    led["students"][k].update(report=None, held=["no report was generated"])
state.write_text(json.dumps(led))
from lata.ledger import Ledger
repaired = Ledger(state.parent)
check(not repaired.students["4"].get("graded") and not repaired.students["5"].get("graded")
      and repaired.students["1"].get("graded"), "students stuck by an earlier stop are made ungraded again on load")

print("a report that genuinely fails")
fx.workdir(cfg_extra); fx.assets("HW3")
fx.tex_zip(Path("data/input/inbox/HW3/t.zip"), {1: ("Student A", "1", "2026-09-30 10:00:00")})
fx.pdf_zip(Path("data/input/inbox/HW3/p.zip"), {11: ("Student A", "1", "2026-09-30 10:05:00")})
calls["fail"] = {"Student A"}
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate()
w.run_pass()
check(graded() == [], "first failure: not recorded, tried again next pass")
w.run_pass()
st = json.loads(state.read_text())["students"]["1"]
check(st.get("graded") and any("no report after 2 attempts" in h for h in st.get("held", [])), "second failure: held for the instructor")
fx.done()
