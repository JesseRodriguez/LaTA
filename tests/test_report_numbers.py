"""Scores on the feedback report: at most two decimals, no floating-point noise."""
import re, runpy, sys
from pathlib import Path
import yaml
import fx
from fx import check

def single(c):
    c['paths'].update(export_dir="data/input/HWX", rubric_file="HWX_rubric.yaml", solution_file="data/solutions/HWX-SOLUTION.tex")
    c['grading']['assignment_name'] = "HWX"
    c['grading']['late_penalties'].update(enabled=True, due_date="2026-10-01 07:59:59", penalty_per_day=0.15, points_awarded_elsewhere=0.7)
fx.workdir(single); fx.assets("HWX")
fx.patch_grader(score=lambda sid, label: None)
from lata import grader as g
real = g.LatexGrader.grade_chunk
def tenth_off(self, *a, **kw):               # every item loses 0.1, like a 0.1 rubric deduction
    items = real(self, *a, **kw)
    for i in items:
        i.score = i.max_score - 0.1
    return items
g.LatexGrader.grade_chunk = tenth_off
fx.tex_zip(Path("data/input/inbox/t.zip"), {1: ("Jane Doe", "1", "2026-10-02 09:00:00")})     # 25h late
fx.pdf_zip(Path("data/input/inbox/p.zip"), {11: ("Jane Doe", "1", "2026-10-02 09:00:00")})
sys.argv = ["grade.py"]
runpy.run_path(str(fx.LATA / "grade.py"), run_name="__main__")

pdf = Path("data/output/HWX_Feedback/Feedback_Jane_Doe.pdf")
tex = Path("data/output/HWX_Feedback/logs/Feedback_Jane_Doe.tex").read_text()
check(pdf.exists(), "report compiles")
noisy = re.findall(r"(?<![\w.])-?\d+\.\d{3,}(?![\w.])", tex)
check(noisy == [], f"no number with more than two decimals on the report ({noisy[:5]})")
check("Missed 0.1 pts" in tex, "a 0.1 deduction shows as 0.1")
# 4 items x 0.1 off: 9.6/10; 25h late -> 2 days -> 30% of (10 + 0.7) = 3.21 -> 6.39
check(re.search(r"\\textbf\{Final Score:\}\s*&\s*\\textbf\{6\.39\}", tex) and "-3.21" in tex,
      "late penalty 3.21 and final score 6.39, rounded to two decimals")
check(re.search(r"\\textbf\{TOTAL\}\s*&\s*\\textbf\{9\.6\}\s*&\s*10\s*&", tex), "totals without trailing zeros (9.6 of 10)")
from lata.gradescope_sync import score_from_report
check(score_from_report(str(pdf)) == (6.39, 10.0), "the watcher still reads the final score off the report")
fx.done()
