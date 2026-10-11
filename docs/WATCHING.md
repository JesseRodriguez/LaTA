# Watching Assignments

Set up rubrics and solutions for a list of assignments, run `uv run grade.py`, and walk
away. LaTA keeps running until you press Ctrl-C. It grades each submission once the
deadline has passed and both files are in, emails feedback (if you allow it), tells
students when a file is missing, and tells you about anything it will not decide on its
own.

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
     auto_send_feedback: true                 # or pass --send / --no-send
     notify_email: you@university.edu
   ```

3. Run it, and leave the Mac awake:

   ```bash
   caffeinate -i uv run grade.py
   ```

   It checks that every rubric and solution exists before starting, and asks for your
   email login once, at startup. `--once` runs a single check and exits.

## Getting submissions in

For now exports come from an inbox: download the Gradescope export `.zip` files into
`data/input/inbox/<assignment name>/`, e.g. `data/input/inbox/ME461_HW1/`. Drop in both
the `.tex` and the PDF assignment's exports. No unzipping or renaming. For a late
submission, re-export and drop the new zip in. It merges with what is already there.

A corrections assignment shares the first pass's PDF assignment, so put that export in
the first pass's folder (`inbox/ME461_HW1/`). Both assignments use it.

## What happens to each student

| Situation | What LaTA does |
|---|---|
| `.tex` and PDF in, deadline passed | Grades it once the student has gone 15 min without resubmitting, charging the later of the two submission times |
| `.tex` in, no PDF | Once the deadline and an hour after the `.tex` have both passed, emails *"PDF missing - late penalty accruing"*. Grades when the PDF arrives. |
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

A student with no first pass at all who submits corrections gets the usual "no original
submission" report.

## Feedback: sent or held

With `auto_send_feedback` on, a report is emailed as soon as it is generated, unless
something about it needs a person:

- a problem failed to grade (schema errors after retries)
- the problems could not be located in the submission (segmentation warning)
- possible prompt injection was detected
- the report did not compile

Held reports stay in the output folder. Look at them, fix what needs fixing, and send
them with `post_grading.py send-feedback`. With auto-sending off, every report waits for
`send-feedback`.

Students graded before the watcher started (by plain `grade.py`) are recognised from
their reports and never graded twice. They are also never emailed automatically.

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
