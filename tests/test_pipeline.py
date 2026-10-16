"""Plain grade.py (one assignment): inbox unpacking, reports, late penalties, --regrade."""
import runpy, sys
from pathlib import Path
import yaml
import fx
from fx import check

def single(c):
    c['paths'].update(export_dir="data/input/HWX", rubric_file="HWX_rubric.yaml", solution_file="data/solutions/HWX-SOLUTION.tex")
    c['grading']['assignment_name'] = "HWX"
    c['grading']['late_penalties'].update(enabled=True, due_date="2026-10-01 07:59:59")
def run(*args):
    sys.argv = ["grade.py", *args]
    runpy.run_path(str(fx.LATA / "grade.py"), run_name="__main__")
    return yaml.safe_load(open("data/output/HWX_Feedback/grading_metadata.yaml"))

for regrade in (False, True):
    fx.workdir(single); fx.assets("HWX"); fx.patch_grader()
    fx.tex_zip(Path("data/input/inbox/t.zip"), {1: ("Jane Doe", "1", "2026-10-01 06:00:00"), 2: ("John Smith", "2", "2026-10-02 09:00:00")})
    fx.pdf_zip(Path("data/input/inbox/p.zip"), {11: ("Jane Doe", "1", "2026-10-01 06:30:00"), 12: ("John Smith", "2", "2026-10-02 08:00:00")})
    if not regrade:
        meta = run()
        check(sorted(p.name for p in Path("data/output/HWX_Feedback").glob("*.pdf")) == ["Feedback_Jane_Doe.pdf", "Feedback_John_Smith.pdf"], "reports generated")
        check(meta["Jane_Doe"]["late_penalty"] == 0 and meta["John_Smith"]["late_penalty"] == 0.4, "Jane on time, John 25h late -> 2 days -> 40%")
        check(meta["John_Smith"]["final_score"] == 6.0 and meta["John_Smith"]["max_score"] == 10.0, "final and max score recorded")
    else:
        meta = run("--regrade", "John Smith")
        check(meta["John_Smith"]["late_penalty"] == 0.4 and meta["John_Smith"]["final_score"] == 6.0,
              "--regrade with no earlier record still records the penalty and final score")
fx.done()
