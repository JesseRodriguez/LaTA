"""Extensions entered on Gradescope: read each check, used for late penalties and notices."""
import json
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check
import fakegs
from fakegs import FakeGS

def cfg_extra(c):
    c['grading']['late_penalties'].update(enabled=True, penalty_per_day=0.2, points_awarded_elsewhere=2)
    c['assignments'] = [{'name': 'HW5', 'rubric_file': 'HW5_rubric.yaml', 'solution_file': 'data/solutions/HW5-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00', 'gradescope': {'tex': 10, 'pdf': 20}}]   # 1:00 AM PDT
    c['watch'] = {'source': 'gradescope', 'gradescope': {'course': 1}, 'send_notices': True,
                  'notify_email': 'instructor@oregonstate.edu'}
fx.workdir(cfg_extra); fx.assets("HW5"); fx.patch_grader()
smtp = fx.patch_emailer()
gs = FakeGS(); fakegs.install(gs)
# Jane: 2 days on the .tex assignment; Max: 1 day on the PDF and 3 days on the .tex (the later wins);
# Lee: a non-date extension, which LaTA cannot read
gs.ext = {10: [{"user_id": "u1", "name": "Jane Doe", "email": "doe1@oregonstate.edu", "due": "2026-10-03T01:00", "type": "absolute"},
               {"user_id": "u6", "name": "Max Ray", "email": "ray6@oregonstate.edu", "due": "2026-10-04T01:00", "type": "absolute"},
               {"user_id": "u5", "name": "Lee Park", "email": "park5@oregonstate.edu", "due": "+2", "type": "relative"}],
          20: [{"user_id": "u6", "name": "Max Ray", "email": "ray6@oregonstate.edu", "due": "2026-10-02T01:00", "type": "absolute"}]}
# Jane and John both submit 10 hours late; Max has only his .tex in
gs.tex = {"1": (101, "2026-10-01 11:00:00"), "2": (102, "2026-10-01 11:00:00"), "6": (106, "2026-10-01 11:00:00")}
gs.pdf = {"1": (201, "2026-10-01 11:00:00"), "2": (202, "2026-10-01 11:00:00")}
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 20, 0)
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()

ext = yaml.safe_load(open("data/input/HW5/extensions.gradescope.yaml"))
check(ext == {"Jane Doe": 48.0, "Max Ray": 72.0}, f"extensions read from Gradescope, in hours past the due date; the later of two wins ({ext})")
meta = yaml.safe_load(open("data/output/HW5_Feedback/grading_metadata.yaml"))
check(meta["Jane_Doe"]["late_penalty"] == 0 and meta["John_Smith"]["late_penalty"] == 0.2,
      "both graded at the due date; Jane (inside her extension) 0%, John 20%")
led = json.load(open("data/output/HW5_Feedback/watch_state.json"))["students"]
check(not (led["6"].get("notices") or {}).get("missing_pdf"), "Max (.tex only, extended) gets no 'late penalty accruing' notice yet")
alerts = [m.get_body().get_content() for m in smtp.sent if m['To'] == "instructor@oregonstate.edu"]
check(any("Lee Park: extension on Gradescope is not a fixed date" in b for b in alerts), "an extension LaTA cannot read is reported")
n = len(smtp.sent); w.run_pass()
check(not any("not a fixed date" in m.get_body().get_content() for m in smtp.sent[n:]), "...once")
gs.ext = {}
w.run_pass()
check(not Path("data/input/HW5/extensions.gradescope.yaml").exists(), "extensions removed on Gradescope disappear here too")
fx.done()
