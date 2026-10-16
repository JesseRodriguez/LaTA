# Watching Assignments

Set up rubrics and solutions for a list of assignments, run `uv run grade.py`, and walk
away. LaTA keeps running until you press Ctrl-C. It fetches submissions from Gradescope,
grades each one once the deadline has passed and both files are in, enters the grade on
Gradescope, tells students when a file is missing, and tells you about anything it will
not decide on its own. Feedback goes out when you release it: after you have graded any
hand-graded questions and published the grades, type `send`.

## Setup

1. Put each assignment's rubric in `data/rubrics/` and its solution in `data/solutions/`.
2. List the assignments in `config.yaml` (the rest of the file is shared by all of them):

   ```yaml
   assignments:
     - name: ME461_HW1
       rubric_file: ME461_HW1_rubric.yaml
       solution_file: data/solutions/ME461-HW1-SOLUTION.tex
       due_date: "2026-10-09 06:59:59"        # GMT
     - name: ME461_HW1_CORRECTIONS
       rubric_file: ME461_HW1_rubric.yaml
       solution_file: data/solutions/ME461-HW1-SOLUTION.tex
       due_date: "2026-10-16 06:59:59"

   watch:
     notify_email: you@university.edu
   ```

   To have LaTA fetch from Gradescope and enter grades there, add each assignment's
   Gradescope IDs (the numbers in its URL) and your course:

   ```yaml
   assignments:
     - name: ME461_HW1
       ...
       gradescope: {tex: 7654321, pdf: 7654320}   # the .tex and PDF assignments

   watch:
     source: gradescope
     gradescope:
       course: 1234567
   ```

   and sign in once (see [Gradescope](#gradescope) below).

3. Run it, and leave the Mac awake:

   ```bash
   caffeinate -i uv run grade.py
   ```

   It checks that every rubric and solution exists before starting, and asks for your
   email login once, at startup. `--once` runs a single check and exits.
   `--dry-run` runs one check that only reports what would happen -- what would be
   graded, entered on Gradescope (and how that compares with grades already there),
   notified and sent -- without doing any of it.

## Adding assignments from Gradescope

Rather than copying IDs and due dates from Gradescope by hand:

```bash
uv run python -m lata.setup_watch            # add to config.watch.yaml (or --config FILE)
uv run python -m lata.setup_watch --list     # only show what is on Gradescope
```

It lists the course's `.tex` assignments, each paired with its PDF assignment by title
("Homework #2 (561) .tex file" with "Homework #2 (561)"; corrections, "... .tex file
(CORRECTIONS)", with the original homework's PDF assignment), and marks those already in
the config. Pick the ones to add; for each it suggests the LaTA name, rubric and solution
from `data/rubrics` and `data/solutions` and the due date from Gradescope (converted to
GMT), all of which you can change. Titles without a course number belong to the course
number in the Gradescope course name that no other title carries ("ME 461/561" with
"(561)" titles elsewhere: 461). It shows the entries before writing them, keeps the rest
of the file as it is, and warns about anything that would stop the watcher starting (a
missing rubric or solution, a corrections assignment whose first pass is neither listed
nor graded).

## Getting submissions in

With `source: gradescope`, LaTA fetches them: each check downloads the grades CSV of the
`.tex` and PDF assignments (PDF submission times come from it, so PDFs are never
downloaded), and exports the `.tex` assignment when its submissions have changed.

Without it, exports come from an inbox: download the Gradescope export `.zip` files into
`data/input/inbox/<assignment name>/`, e.g. `data/input/inbox/ME461_HW1/`. Drop in both
the `.tex` and the PDF assignment's exports. No unzipping or renaming. For a late
submission, re-export and drop the new zip in. It merges with what is already there.

A corrections assignment shares the first pass's PDF assignment, so put that export in
the first pass's folder (`inbox/ME461_HW1/`). Both assignments use it.

## What happens to each student

| Situation | What LaTA does |
|---|---|
| `.tex` and PDF in, deadline passed | Grades it once the student has gone 15 min without resubmitting, charging the later of the two submission times |
| `.tex` in, no PDF | Once the deadline and an hour after the `.tex` have both passed, emails *"PDF missing - late penalty accruing"*. Grades when the PDF arrives. (Corrections: see below.) |
| PDF in, no `.tex` | Emails *".tex missing"* on the same schedule |
| Submitted to the `.tex` assignment without a `.tex` file | Same `.tex` notice if there is a PDF; otherwise flags it to you |
| Already graded, then submits a new `.tex` | Flags it to you. Never regraded automatically. |
| Before the deadline (plus any extension in `extensions.yaml`) | Waits |

Each notice is sent once. Notices and feedback are recorded in each assignment's
`email_delivery.yaml`, so `post_grading.py send-feedback` will not repeat them.

### Corrections

A `_CORRECTIONS` assignment is graded in corrections mode after its deadline. Because
corrections go to the same PDF assignment as the first pass, LaTA keeps each student's
first-pass submission time *frozen at the moment they were graded*. A corrections PDF
never changes a first-pass late penalty, and for corrections only a PDF newer than that
frozen time counts.

This also catches the late first pass that is easy to miss: a student who submits the
main assignment for the first time after corrections have opened. They have no graded
first pass, so their submission is graded as a (late) first pass, not mistaken for a
correction. Their corrections wait until that first pass is graded.

A corrections `.tex` without a new PDF is incomplete. It is still graded at the
corrections deadline, but the feedback email tells the student that, because they did not
resubmit their compiled PDF, their corrections grade will not be applied. If the PDF turns
up after that email, you are flagged to decide whether to apply it.

A student with no first pass at all who submits corrections gets the usual "no original
submission" report.

## Gradescope

Sign in once; LaTA keeps the session (in `~/.lata/gradescope-profile/session.json`,
readable only by you) and never sees your password:

```bash
uv sync --extra gradescope && uv run playwright install chromium   # first time only
uv run --extra gradescope python -m lata.gradescope login           # opens a browser window
```

When the session expires, LaTA emails you (`notify_email`) and fetches nothing until you
run `login` again. `python -m lata.gradescope check` tests it.

**Entering grades.** Each graded student's score goes into the PDF assignment's largest
question (or `gradescope: {..., question: "HW2"}`), which should be negatively scored, the
way you would enter it: LaTA's points off -- its rubric total minus the report's final
score, late penalty included -- as the point adjustment, or the zero-point "Correct"
rubric item for a perfect score. A student who submitted a PDF but no `.tex` gets the
rubric item worth minus the question's points (or that adjustment) once their notice has
gone out; if they submit later, their real grade replaces it. Other questions are left
for you.

LaTA only writes to question submissions nobody has graded, or that it graded itself,
and reads each grade back after saving. A grade already entered by hand that differs
from LaTA's is left alone and reported to you; that student's feedback waits until you
resolve it (and send it with `post_grading.py send-feedback`). Corrections are entered
on the student's resubmitted PDF -- the new, ungraded submission -- so Gradescope keeps
the first-pass grade in its history; incomplete corrections (no new PDF) are not entered.

LaTA cannot tell handwritten or otherwise invalid work from its side of things; look
through the submissions after feedback goes out.

## Releasing feedback

Feedback waits for you. When students' grades are on Gradescope, LaTA tells you (email
and console) that feedback is ready. Then:

1. grade the hand-graded questions on Gradescope,
2. publish the grades on Gradescope,
3. type `send ME461_HW1` (or just `send` for everything ready) into the running watcher.

`send` checks that the assignment is published and that every waiting student is fully
graded on Gradescope (it lists anyone who is not; `send ME461_HW1 --force` sends anyway),
posts the grades to Canvas, and emails the reports. Students who submit later are graded
and entered the same way, and wait for the next `send`.

Other commands: `status`, `check` (check for submissions now), `help`. The watcher keeps
checking for submissions while it waits for you to type.

## Sending automatically

With `auto_send_feedback: true` (or `--send`), a clean report is emailed as soon as it is
generated -- for assignments with nothing graded by hand. Grades are still entered on
Gradescope; publishing and the Canvas sync are left to you. A report is held back when
something about it needs a person:

- a problem failed to grade (schema errors after retries)
- the problems could not be located in the submission (segmentation warning)
- possible prompt injection was detected
- the report did not compile

Held reports (in either mode) stay in the output folder. Look at them, fix what needs
fixing, and send them with `post_grading.py send-feedback`.

Students graded before the watcher started (by plain `grade.py`) are recognised from
their reports and never graded twice. Their grades are entered on Gradescope like
anyone's, and they are included when you `send`, but never emailed automatically.

## Hearing from it

Every check prints a status line per assignment, e.g.

```
   ME461_HW1: 41 graded, 39 sent, 1 held | waiting: 2 waiting for PDF, 1 settling (recent submission)
```

Everything it does is appended to `data/output/watch_log.txt`. Anything needing you
(held reports, flagged resubmissions, failed emails, export problems) is emailed to
`watch.notify_email`, or shown as a desktop notification if that is not set.

## Files

- `data/output/<assignment>_Feedback/watch_state.json`: what LaTA knows about each
  student: graded when and from which submission times, notices, flags. Safe to read.
  If you delete it, LaTA rebuilds what it can from the reports, but first-pass times
  frozen before corrections opened cannot be recovered, so keep it.
- `data/output/watch_log.txt`: the running log.

## The model

A GGUF grader (`grader.model: "qwen..."`) is started the first time something needs
grading and kept loaded between checks. After `unload_model_after_idle_minutes` (default
120) without grading it is stopped to free memory, and restarted on the next
submission.
