"""Extensions delay late penalties and notices, not grading."""
import json
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check

def cfg_extra(c):
    c['grading']['late_penalties'].update(enabled=True, penalty_per_day=0.2)
    c['assignments'] = [{'name': 'HW5', 'rubric_file': 'HW5_rubric.yaml', 'solution_file': 'data/solutions/HW5-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00'}]
    c['watch'] = {'send_notices': False}
fx.workdir(cfg_extra); fx.assets("HW5"); fx.patch_grader(); fx.patch_emailer()
# Both submit 10 hours late; Jane has a 24-hour extension
fx.tex_zip(Path("data/input/inbox/HW5/t.zip"), {1: ("Jane Doe", "1", "2026-10-01 18:00:00"), 2: ("John Smith", "2", "2026-10-01 18:00:00")})
fx.pdf_zip(Path("data/input/inbox/HW5/p.zip"), {11: ("Jane Doe", "1", "2026-10-01 18:00:00"), 12: ("John Smith", "2", "2026-10-01 18:00:00")})
Path("data/input/HW5").mkdir(parents=True, exist_ok=True)
yaml.safe_dump({"Jane Doe": 24}, open("data/input/HW5/extensions.yaml", "w"))
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 18, 30)          # inside Jane's extension
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
led = json.load(open("data/output/HW5_Feedback/watch_state.json"))["students"]
check(led["1"].get("graded") and led["2"].get("graded"), "both graded now -- Jane is not held until her extension ends")
meta = yaml.safe_load(open("data/output/HW5_Feedback/grading_metadata.yaml"))
check(meta["Jane_Doe"]["late_penalty"] == 0 and meta["John_Smith"]["late_penalty"] == 0.2,
      f"late penalty: Jane 0% (within extension), John 20% ({meta['Jane_Doe']['late_penalty']}, {meta['John_Smith']['late_penalty']})")
fx.done()
