"""Corrections: sections that scored 0 originally cannot recover points -- in the totals too."""
import re, runpy, sys
from pathlib import Path
import yaml
import fx
from fx import check

def single(name):
    def extra(c):
        c['paths'].update(export_dir=f"data/input/{name}", rubric_file="HWZ_rubric.yaml", solution_file="data/solutions/HWZ-SOLUTION.tex")
        c['grading']['assignment_name'] = name
        c['grading']['late_penalties'].update(enabled=True, due_date="2026-10-01 07:59:59")
    return extra
fx.workdir(single("HWZ")); fx.assets("HWZ")
phase = {"name": "first"}
fx.patch_grader(score=lambda sid, label: 0 if (phase["name"] == "first" and "Kim" in sid and label == "Problem 1") else None)
def run(name, students):
    cfg = yaml.safe_load(open("config.yaml")); single(name)(cfg); yaml.safe_dump(cfg, open("config.yaml", "w"))
    inbox = Path("data/input/inbox"); [p.unlink() for p in inbox.glob("*.zip")] if inbox.exists() else None
    fx.tex_zip(inbox / "t.zip", {i: (n, s, "2026-09-30 10:00:00") for i, (n, s) in students.items()})
    fx.pdf_zip(inbox / "p.zip", {10 + i: (n, s, "2026-09-30 10:05:00") for i, (n, s) in students.items()})
    sys.argv = ["grade.py"]
    runpy.run_path(str(fx.LATA / "grade.py"), run_name="__main__")
students = {1: ("Kim Lee", "3"), 2: ("Jane Doe", "1")}
run("HWZ", students)
orig = yaml.safe_load(open("data/output/HWZ_Feedback/grading_metadata.yaml"))
check(orig["Kim_Lee"]["zero_scored_items"] == ["1a"] and orig["Jane_Doe"]["zero_scored_items"] == [], "first pass: Kim scored 0 on Problem 1 (a)")

phase["name"] = "corrections"                  # full marks on everything in corrections
run("HWZ_CORRECTIONS", students)
meta = yaml.safe_load(open("data/output/HWZ_CORRECTIONS_Feedback/grading_metadata.yaml"))
check(meta["Kim_Lee"]["final_score"] == 5.0, f"Kim's corrections: Problem 1 stays 0 -> 5/10 recorded (and entered on Gradescope), not 10 ({meta['Kim_Lee']['final_score']})")
check(meta["Jane_Doe"]["final_score"] == 10.0, "Jane (nothing zero-scored) gets full corrections credit")
tex = Path("data/output/HWZ_CORRECTIONS_Feedback/logs/Feedback_Kim_Lee.tex").read_text()
check(re.search(r"\\textbf\{TOTAL\}\s*&\s*\\textbf\{5\}\s*&\s*10", tex) and "ZERO-SCORED ITEMS" in tex,
      "the report's total is 5 of 10, with the zero-scored box")
check("No points can be recovered here" in tex, "the zero-scored items say why on the report")
from lata.gradescope_sync import score_from_report
check(score_from_report("data/output/HWZ_CORRECTIONS_Feedback/Feedback_Kim_Lee.pdf") == (5.0, 10.0), "the watcher reads 5 off the report as well")
fx.done()
