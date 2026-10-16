"""The watcher against a fake Gradescope: fetching, entering grades, releasing with `send`."""
import json, copy
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check
import fakegs
from fakegs import FakeGS

def cfg_extra(c):
    c['grading']['late_penalties'].update(enabled=True, penalty_per_day=0.2, points_awarded_elsewhere=4)
    c['assignments'] = [{'name': 'HW2', 'rubric_file': 'HW2_rubric.yaml', 'solution_file': 'data/solutions/HW2-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00', 'gradescope': {'tex': 10, 'pdf': 20}}]
    c['watch'] = {'source': 'gradescope', 'notify_email': 'instructor@oregonstate.edu', 'gradescope': {'course': 1}}
fx.workdir(cfg_extra); fx.assets("HW2")
fx.patch_grader(score=lambda sid, label: 1 if "Kim" in sid else None)
smtp = fx.patch_emailer()
gs = FakeGS(); fakegs.install(gs)
import lata.watch as W
from lata.pipeline import GradingOptions
CLOCK = [None]; W.utcnow = lambda: CLOCK[0]
at = lambda s: CLOCK.__setitem__(0, datetime.strptime(s, "%Y-%m-%d %H:%M"))
cfg = yaml.safe_load(open("config.yaml"))
to = lambda msgs: sorted(m['To'] for m in msgs if list(m.iter_attachments()))

gs.tex = {"1": (101, "2026-09-30 10:00:00"), "3": (103, "2026-09-30 10:00:00"), "5": (105, "2026-09-30 10:00:00")}
gs.pdf = {"1": (201, "2026-09-30 10:05:00"), "2": (202, "2026-09-30 10:05:00"), "3": (203, "2026-09-30 10:05:00"), "5": (205, "2026-09-30 10:05:00")}
gs.evals[2050] = {"rubric": set(), "points": -3.0, "graded": True}       # Lee, graded by hand: 5

print("dry run")
at("2026-10-01 09:30")
W.Watcher(cfg, "config.yaml", GradingOptions(), dry_run=True).run_pass()
check(gs.saves == [] and smtp.sent == [] and not Path("data/output/HW2_Feedback/watch_state.json").exists(), "nothing entered, sent or saved")
check(Path("data/input/HW2/submission_metadata.yml").exists() and Path("data/input/HW2/pdf_submission_metadata.yml").exists(), "but exports fetched")

print("at the deadline")
w = W.Watcher(cfg, "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
saves = {q: (r, p) for q, r, p in gs.saves}
check(saves.get(2010) == ([1], 0.0), "perfect: 'Correct' rubric item")
check(saves.get(2030) == ([], -6.0), "4/10: adjustment -6 (points off, on an 8-point question)")
check(saves.get(2020) == ([2], 0.0), "PDF, no .tex: zero rubric item")
check(2050 not in saves, "hand-graded grade that differs: left alone")
alert = [m for m in smtp.sent if m['To'] == "instructor@oregonstate.edu"][-1].get_body().get_content()
check("type `send HW2`" in alert and "already graded on Gradescope as 5; LaTA has 8" in alert, "instructor told: ready to send, and the conflict")
check(to(smtp.sent) == [], "no feedback yet")
n = len(smtp.sent); w.run_pass()
check(len(gs.saves) == 3 and len(smtp.sent) == n, "next pass: nothing re-entered or re-announced")

print("release")
W.handle_command(w, "send HW2"); check(gs.canvas_posts == 0 and len(smtp.sent) == n, "not published: refused")
gs.published = True
W.handle_command(w, "send HW2"); check(gs.canvas_posts == 0 and len(smtp.sent) == n, "hand-graded questions open: refused")
gs.sketch_graded |= {"1", "2", "3", "5"}
W.handle_command(w, "send HW2")
check(gs.canvas_posts == 1 and to(smtp.sent[n:]) == ["doe1@oregonstate.edu", "lee3@oregonstate.edu"], "Canvas posted; Jane and Kim emailed")

print("a straggler")
gs.tex["6"] = (106, "2026-10-01 20:00:00"); gs.pdf["6"] = (206, "2026-10-01 20:05:00")
at("2026-10-02 04:00"); n = len(smtp.sent); w.run_pass()
check(dict((q, p) for q, r, p in gs.saves).get(2060) == -2.8, "19h late, perfect: 10 - 20% of 14 = 7.2 -> -2.8")
W.handle_command(w, "send"); check(gs.canvas_posts == 1, "sketch not graded: refused")
gs.sketch_graded.add("6"); W.handle_command(w, "send")
check(gs.canvas_posts == 2 and to(smtp.sent[n:]) == ["ray6@oregonstate.edu"], "then posted again, only Max emailed")

print("scores come from the report, not today's settings")
led = json.load(open("data/output/HW2_Feedback/watch_state.json"))["students"]
check(led["5"]["gradescope"]["status"] == "conflict", "conflict stays out of releases")
changed = copy.deepcopy(cfg); changed['grading']['late_penalties']['points_awarded_elsewhere'] = 0
meta_path = Path("data/output/HW2_Feedback/grading_metadata.yaml")
meta = yaml.safe_load(meta_path.read_text())
for m in meta.values():                                # as reports graded before final scores were recorded
    m.pop('final_score', None)
meta_path.write_text(yaml.safe_dump(meta))
n = len(gs.saves); w2 = W.Watcher(changed, "config.yaml", GradingOptions()); w2.authenticate(); w2.run_pass()
check(gs.saves[n:] == [], "points_awarded_elsewhere changed afterwards: nothing re-entered (scores read from the reports)")

print("per-assignment overrides")
c2 = copy.deepcopy(cfg); c2['assignments'][0]['grading'] = {'late_penalties': {'points_awarded_elsewhere': 6}}
late = W.build_assignments(c2)[0].config['grading']['late_penalties']
check(late['points_awarded_elsewhere'] == 6 and late['enabled'] and late['due_date'] == '2026-10-01 08:00:00',
      "overriding points_awarded_elsewhere keeps the rest of late_penalties")
fx.done()
