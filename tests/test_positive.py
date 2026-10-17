"""Grade entry on a positively scored LaTA question lands on the same Gradescope score."""
from datetime import datetime
import yaml
import fx
from fx import check
import fakegs
from fakegs import FakeGS

def cfg_extra(c):
    c['grading']['late_penalties'].update(enabled=True, penalty_per_day=0.2, points_awarded_elsewhere=2)
    c['assignments'] = [{'name': 'HW3', 'rubric_file': 'HW3_rubric.yaml', 'solution_file': 'data/solutions/HW3-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00', 'gradescope': {'tex': 10, 'pdf': 20}}]
    c['watch'] = {'source': 'gradescope', 'gradescope': {'course': 1}, 'send_notices': True}
fx.workdir(cfg_extra); fx.assets("HW3")
fx.patch_grader(score=lambda sid, label: 1 if "Kim" in sid else None)
fx.patch_emailer()
gs = FakeGS(); gs.scoring = "positive"; fakegs.install(gs)
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 12, 0)
gs.tex = {"1": (101, "2026-09-30 10:00:00"), "3": (103, "2026-09-30 10:00:00")}
gs.pdf = {"1": (201, "2026-09-30 10:05:00"), "2": (202, "2026-09-30 10:05:00"), "3": (203, "2026-09-30 10:05:00")}
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
saves = {q: (r, p) for q, r, p in gs.saves}
check(saves.get(2010) == ([], 8.0) and gs.score_of(2010) == 8.0, "perfect on a positive 8-point question: 8 entered, shows 8")
check(saves.get(2030) == ([], 2.0) and gs.score_of(2030) == 2.0, "6 off: 2 entered, shows 2 (same as negative scoring would)")
check(saves.get(2020) == ([], 0.0) and gs.evals[2020]["graded"], "missing .tex: 0, marked graded")
fx.done()
