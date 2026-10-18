"""watch.auto_send_feedback with Gradescope: release per student whenever `send` would succeed."""
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
    c['watch'] = {'source': 'gradescope', 'gradescope': {'course': 1}, 'auto_send_feedback': True,
                  'notify_email': 'instructor@oregonstate.edu'}
fx.workdir(cfg_extra); fx.assets("HW3")
fx.patch_grader()
smtp = fx.patch_emailer()
gs = FakeGS(); fakegs.install(gs)
import lata.watch as W
from lata.pipeline import GradingOptions
CLOCK = [None]; W.utcnow = lambda: CLOCK[0]
at = lambda s: CLOCK.__setitem__(0, datetime.strptime(s, "%Y-%m-%d %H:%M"))
fb = lambda msgs: sorted(m['To'] for m in msgs if list(m.iter_attachments()))
notes = lambda msgs: [m.get_body().get_content() for m in msgs if m['To'] == "instructor@oregonstate.edu"]

gs.tex = {"1": (101, "2026-09-30 10:00:00"), "3": (103, "2026-09-30 10:00:00")}
gs.pdf = {"1": (201, "2026-09-30 10:05:00"), "3": (203, "2026-09-30 10:05:00")}
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate()

at("2026-09-30 20:00"); w.run_pass()
check(gs.saves == [] and fb(smtp.sent) == [], "before the deadline: nothing graded, entered or sent")

at("2026-10-01 09:00"); n = len(smtp.sent); w.run_pass()
check(len(gs.saves) == 2 and fb(smtp.sent[n:]) == [] and gs.canvas_posts == 0, "graded and entered, but not published: nothing sent, no Canvas sync")
check(any("goes out automatically once you publish" in b for b in notes(smtp.sent[n:])), "instructor told publishing releases it")
n = len(smtp.sent); w.run_pass()
check(smtp.sent[n:] == [], "next pass: no repeat of that note")

gs.published = True; gs.sketch_graded.add("1")
n = len(smtp.sent); w.run_pass()
check(fb(smtp.sent[n:]) == ["doe1@oregonstate.edu"] and gs.canvas_posts == 1, "published; Jane's sketch graded: Canvas sync, then only Jane emailed")
gs.sketch_graded.add("3")
n = len(smtp.sent); w.run_pass()
check(fb(smtp.sent[n:]) == ["lee3@oregonstate.edu"] and gs.canvas_posts == 2, "Kim's sketch graded: synced and emailed on the next pass")

print("a straggler")
gs.tex["6"] = (106, "2026-10-01 20:00:00"); gs.pdf["6"] = (206, "2026-10-01 20:05:00")
at("2026-10-02 04:00"); n = len(smtp.sent); w.run_pass()
check(dict((q, p) for q, r, p in gs.saves).get(2060) is not None and fb(smtp.sent[n:]) == [], "graded and entered; waits for the sketch")
gs.sketch_graded.add("6")
n = len(smtp.sent); w.run_pass()
check(fb(smtp.sent[n:]) == ["ray6@oregonstate.edu"] and gs.canvas_posts == 3, "sketch graded: synced and emailed, nothing typed")

print("no hand-graded questions")
fx.workdir(cfg_extra); fx.assets("HW3")
gs2 = FakeGS(); fakegs.install(gs2); gs2.sketch_graded = set(fakegs.NAMES)   # nothing waits on hand grading
gs2.tex = {"1": (101, "2026-09-30 10:00:00")}; gs2.pdf = {"1": (201, "2026-09-30 10:05:00")}
w2 = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w2.authenticate()
at("2026-10-01 09:00"); n = len(smtp.sent); w2.run_pass()
check(fb(smtp.sent[n:]) == [], "first batch waits for the one publish")
gs2.published = True; n = len(smtp.sent); w2.run_pass()
check(fb(smtp.sent[n:]) == ["doe1@oregonstate.edu"] and gs2.canvas_posts == 1, "published: released on the next pass")
gs2.tex["3"] = (103, "2026-10-01 20:00:00"); gs2.pdf["3"] = (203, "2026-10-01 20:05:00")
at("2026-10-02 04:00"); n = len(smtp.sent); w2.run_pass()
check(fb(smtp.sent[n:]) == ["lee3@oregonstate.edu"] and gs2.canvas_posts == 2, "straggler: entered, synced and emailed in the same pass")
fx.done()
