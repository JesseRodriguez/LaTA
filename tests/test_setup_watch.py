"""lata.setup_watch: pairing Gradescope assignments, suggestions, and writing the config."""
import builtins
from pathlib import Path
import yaml
import fx
from fx import check
from lata import setup_watch as S
from lata.setup_watch import GSAssignment, base_title, pair_assignments, suggest, add_entries, course_number_for

TITLES = [(1, "Homework #2"), (2, "Homework #2 (561)"), (3, "Homework #2 .tex file"), (4, "Homework #2 (561) .tex file"),
          (5, "Homework #2 .tex file (CORRECTIONS)"), (6, "Homework #2 (561) .tex File (Corrections)"),
          (7, "Homework #4 .tex file"), (8, "Homework #4")]
A = [GSAssignment(id=i, title=t, due_local="2026-10-18T22:00", hard_due_local=None, published=True,
                  due_utc=S.to_utc("2026-10-18T22:00", "America/Los_Angeles"), total_points=12.0) for i, t in TITLES]
titles = [t for _, t in TITLES]

pairs = {p.tex.id: p for p in pair_assignments(A)}
check(sorted(pairs) == [3, 4, 5, 6, 7], "every .tex assignment is paired")
check(pairs[3].pdf.id == 1 and pairs[4].pdf.id == 2, "first passes paired with their PDF assignments")
check(pairs[5].pdf.id == 1 and pairs[6].pdf.id == 2 and pairs[5].corrections and pairs[6].corrections,
      "corrections paired with the original PDF assignment (case-insensitive)")
check(base_title("Homework #1 (561) .tex File") == "homework #1 (561)", "title normalisation")
check(A[0].due_utc == "2026-10-19 05:00:00", "local due date converted to GMT (PDT +7h)")
check(course_number_for("Homework #2", "ME 461/561", titles) == "461" and course_number_for("Homework #2 (561)", "ME 461/561", titles) == "561",
      "untagged titles get the course number no title is tagged with")

fx.workdir()
for f in ("ME461_HW2_rubric.yaml", "ME561_HW2_rubric.yaml", "ME373_HW2_rubric.yaml", "ME461_HW3_rubric.yaml"):
    Path("data/rubrics", f).write_text(yaml.safe_dump(fx.RUBRIC))
for f in ("ME461-HW2-SOLUTION.tex", "ME561-HW2-SOLUTION.tex", "ME499-HW2-SOLUTION.tex"):
    Path("data/solutions", f).write_text(fx.SOLUTION)
s = suggest(pairs[5], "ME 461/561", titles, Path("data/rubrics"), Path("data/solutions"))
check((s.name, s.rubric, s.solution) == ("ME461_HW2_CORRECTIONS", "ME461_HW2_rubric.yaml", "data/solutions/ME461-HW2-SOLUTION.tex"),
      f"461 corrections: base rubric and solution, _CORRECTIONS name ({s.name}, {s.rubric})")
s = suggest(pairs[6], "ME 461/561", titles, Path("data/rubrics"), Path("data/solutions"))
check((s.name, s.rubric) == ("ME561_HW2_CORRECTIONS", "ME561_HW2_rubric.yaml"), "561 corrections suggestion")
s = suggest(pairs[7], "ME 461/561", titles, Path("data/rubrics"), Path("data/solutions"))
check(s.rubric is None and s.solution is None, "no HW4 rubric yet: nothing suggested (other courses' files excluded)")

# Writing: into an existing assignments block that is followed by watch:, keeping comments
Path("data/output/ME461_HW2_Feedback").mkdir(parents=True); Path("data/output/ME561_HW2_Feedback").mkdir(parents=True)
cfg_text = Path("config.yaml").read_text().rstrip() + '''

# my watch setup
assignments:
  - name: ME461_HW3
    rubric_file: ME461_HW3_rubric.yaml
    solution_file: data/solutions/ME461-HW2-SOLUTION.tex
    due_date: "2026-10-17 05:00:00"
    gradescope: {tex: 9, pdf: 8}

watch:
  source: gradescope   # keep me
  gradescope:
    course: 1234567
'''
Path("config.yaml").write_text(cfg_text)
answers = iter(["3,4,5"] + [""] * 5 + [""] * 4 + ["4"] + [""] * 3 + ["y"])
# rows 3 and 4: name, rubric, solution, due, hand-graded points (row 3 accepts the default, row 4 types 4);
# row 5 (HW4) stops after the solution
builtins.input = lambda prompt="": next(answers)
class NoSession:
    def __init__(self, *a): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
import lata.gradescope
lata.gradescope.Gradescope = NoSession
S.fetch_assignments = lambda gs, course: (A, "ME 461/561", "America/Los_Angeles")
rc = S.main(["--config", "config.yaml"])
text = Path("config.yaml").read_text()
cfg = yaml.safe_load(text)
names = [a["name"] for a in cfg["assignments"]]
check(rc == 0 and names == ["ME461_HW3", "ME461_HW2_CORRECTIONS", "ME561_HW2_CORRECTIONS"],
      f"two corrections added after the existing entry; HW4 skipped for lack of a rubric ({names})")
check("# keep me" in text and "# my watch setup" in text and cfg["watch"]["gradescope"]["course"] == 1234567, "comments and the watch block kept")
e = cfg["assignments"][1]
check(e["gradescope"] == {"tex": 5, "pdf": 1} and e["due_date"] == "2026-10-19 05:00:00", "IDs and GMT due date written")
check(e["grading"]["late_penalties"]["points_awarded_elsewhere"] == 2 and cfg["assignments"][2]["grading"]["late_penalties"]["points_awarded_elsewhere"] == 4,
      "hand-graded points: default = PDF total 12 - rubric 10 = 2; a typed value is kept")

import lata.watch as W
built = [a.name for a in W.build_assignments(cfg)]
late = W.build_assignments(cfg)[1].config["grading"]["late_penalties"]
check(late["points_awarded_elsewhere"] == 2 and late["due_date"] == "2026-10-19 05:00:00" and "penalty_per_day" in late,
      "the per-assignment value merges into late_penalties without dropping the rest")
check(built[-2:] == ["ME461_HW2_CORRECTIONS", "ME561_HW2_CORRECTIONS"] and W.build_assignments(cfg)[-1].corrections,
      "the watcher accepts the new entries (corrections mode)")

# Into a config with no assignments or watch block at all
Path("c2.yaml").write_text("grading:\n  concurrency: 1\n")
add_entries(Path("c2.yaml"), [S.entry_text("ME461_HW2", "ME461_HW2_rubric.yaml", "data/solutions/ME461-HW2-SOLUTION.tex",
                                           "2026-10-10 05:00:00", pairs[3], "2026-10-09T22:00", "America/Los_Angeles")], 42)
c2 = yaml.safe_load(Path("c2.yaml").read_text())
check(c2["assignments"][0]["gradescope"] == {"tex": 3, "pdf": 1} and c2["watch"]["source"] == "gradescope" and c2["watch"]["gradescope"]["course"] == 42,
      "creates the assignments list and a gradescope watch block when missing")
fx.done()
