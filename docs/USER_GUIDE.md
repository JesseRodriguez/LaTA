# LaTA User Guide: from deadline to feedback, unattended

This guide covers LaTA's watched workflow: you set up a term's assignments once, start
`grade.py`, and LaTA takes each submission from Gradescope to a graded feedback report,
enters the grade on Gradescope, and emails the feedback when you release it. It runs
until you stop it, so late submissions and corrections are handled as they come in.

If you would rather grade one exported assignment at a time, see
[Getting Started](GETTING_STARTED.md); everything about rubrics, models and reports there
applies here too. For the mechanics behind each rule, see [Watching](WATCHING.md).

**Contents**

1. [How it works](#1-how-it-works)
2. [One-time setup](#2-one-time-setup)
3. [Setting up Gradescope assignments](#3-setting-up-gradescope-assignments)
4. [Adding assignments](#4-adding-assignments)
5. [Running the watcher](#5-running-the-watcher)
6. [Releasing feedback](#6-releasing-feedback)
7. [Late submissions, missing files and resubmissions](#7-late-submissions-missing-files-and-resubmissions)
8. [Corrections](#8-corrections)
9. [When LaTA needs you](#9-when-lata-needs-you)
10. [Stopping, restarting and changing things](#10-stopping-restarting-and-changing-things)
11. [Reference](#11-reference)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. How it works

Each homework is two Gradescope assignments: a **`.tex` assignment**, where students
upload their LaTeX source (LaTA grades this), and a **PDF assignment**, where they upload
the compiled PDF (Gradescope keeps the grades here, and your TAs grade anything drawn by
hand). Late penalties use the later of the two submission times.

Every few minutes the watcher:

1. **fetches** each assignment's submission list from Gradescope -- and the `.tex`
   files themselves when that list has changed;
2. **decides**, per student, what is due -- grading, an "incomplete submission" email,
   or a note to you -- from what it has seen and done before;
3. **grades** students whose deadline has passed and whose submission is complete,
   writing their feedback reports;
4. **enters** each grade on Gradescope, in the LaTA question's point-adjustment field,
   the way you would by hand;
5. **tells you** when feedback is ready. Nothing is emailed until you have graded the
   hand-graded questions, published, and typed `send`.

```
 Gradescope ──fetch──▶ LaTA ──grade──▶ feedback PDFs
     ▲                   │
     └──enter grades─────┘      you: grade sketches, publish, type `send`
                                LaTA: post to Canvas, email feedback
```

---

## 2. One-time setup

### Software

```bash
git clone https://github.com/JesseRodriguez/LaTA.git && cd LaTA
uv sync --extra gradescope            # LaTA plus Playwright, for Gradescope
uv run playwright install chromium    # the browser Playwright drives (~550 MB)
brew install --cask mactex            # pdflatex, for the feedback reports
```

You also need a model server:

- **llama.cpp** (recommended, for GGUF models): `brew install llama.cpp`, or build it
  from source into `~/llama.cpp/build/bin/`. LaTA finds `llama-server` on your `PATH`
  or there (or set `system.llama_server`). New model architectures need a recent build;
  the reference setup uses build 274 (commit `427291b`).
- **Ollama**, for Ollama models and for the segmentation model.

### The grading model

The reference setup grades with Qwen3.8-Flash-Next (Unsloth's UD-Q4_K_XL GGUF, about
104 GB) on a 256 GB Mac Studio:

```bash
uvx --from huggingface_hub hf download unsloth/Qwen3.8-Flash-Next-GGUF \
    --include "UD-Q4_K_XL/*" "mmproj-F16.gguf" --local-dir ~/models/Qwen3.8-Flash-Next-GGUF
ollama pull gpt-oss:20b               # segmentation fallback (see below)
```

Then, in `config.yaml`, name it:

```yaml
system:
  model_dirs: ["~/models"]          # where your GGUF files are (this is the default)
  grader:
    model: "Qwen"                   # matched loosely against GGUF files and Ollama models
  segmenter:
    model_name: "gpt-oss:20b"
grading:
  concurrency: 4                    # problems graded at once (slots on the model server)
```

LaTA starts `llama-server` itself, with the settings that model needs (for
Qwen3.8-Flash-Next: `reasoning_effort: low`, temperature 0.1, top_p 0.9, presence penalty
0.2, 16k output tokens, 32k context per slot, no repeat penalty, no reasoning budget), and
stops it when it is idle for two hours. On that setup, four slots grade about 40 problems
an hour.

The segmentation model splits a submission into problems only when its problems cannot
be found by pattern (`\Prob{1}`, `\section{Problem 1}`); with a consistent template it is
rarely used. Ollama serves one request at a time unless told otherwise; for
`concurrency > 1` with an Ollama grader, run `launchctl setenv OLLAMA_NUM_PARALLEL 4` and
restart Ollama.

### Email

LaTA emails feedback, "incomplete submission" notices, and notes to you. On a Mac the
simplest backend uses Mail.app (no password needed):

```yaml
email:
  backend: "applescript"
  applescript:
    from_account: "you@university.edu"   # or leave null to be asked at startup
```

See [Email Quick Start](email/EMAIL_QUICK_START.md) for SMTP.

### Signing in to Gradescope

```bash
uv run python -m lata.gradescope login
```

A browser window opens; sign in as usual (SSO is fine). It closes once you are in, and
LaTA keeps the session in `~/.lata/gradescope-profile/session.json` (readable only by
you). It never sees your password. Check the session any time with
`uv run python -m lata.gradescope check`; when it eventually expires, LaTA emails you and
you run `login` again.

---

## 3. Setting up Gradescope assignments

LaTA works with your Gradescope assignments as they are, given a few conventions:

- **Name each pair consistently.** The `.tex` assignment's title is the PDF assignment's
  title plus ".tex file": `Homework #3` and `Homework #3 .tex file`;
  `Homework #3 (561)` and `Homework #3 (561) .tex file`. Corrections:
  `Homework #2 .tex file (CORRECTIONS)` (paired with the PDF assignment `Homework #2`).
- **Give the PDF assignment one question for LaTA's grade** -- the largest question
  (or name it in the config) -- with a single zero-point rubric item, "Correct".
  **Negative scoring is recommended**: LaTA then enters the points off, exactly as you
  would. Positive scoring works too (LaTA enters the score). Put anything graded by hand
  (sketches, plots) in other questions.
- **Optionally, a rubric item worth minus the full question** ("No .tex file") -- LaTA
  applies it to students who submitted a PDF but no `.tex`.
- **Corrections** go in a separate `.tex` assignment; students resubmit their PDF to the
  original PDF assignment, so extend its late due date to the corrections deadline.
- **Link the PDF assignment to Canvas** if you want LaTA to post grades there on `send`.
- **Enter extensions on Gradescope**, on either assignment of the pair; LaTA picks them up.

---

## 4. Adding assignments

For each homework, write the rubric (`data/rubrics/<NAME>_rubric.yaml`, see
`data/rubrics/example_rubric.yaml`) and the reference solution
(`data/solutions/<...>.tex`). The rubric should cover only what LaTA grades: its total is
LaTA's part of the assignment.

Then add the assignment to the watch config without looking anything up:

```bash
uv run python -m lata.setup_watch --config config.watch.yaml
```

It lists the course's `.tex` assignments, each paired with its PDF assignment, marking
those already in the config:

```
   #  .tex assignment                         PDF assignment       due               in config.watch.yaml
   7  Homework #3 .tex file                   Homework #3          2026-10-16 22:00  ME461_HW3
   9  Homework #2 .tex file (CORRECTIONS)     Homework #2          2026-10-18 22:00
  11  Homework #4 .tex file                   Homework #4          2026-10-23 22:00
```

Pick rows; for each it suggests, and lets you change:

- the **LaTA name** (from the rubric's file name; `_CORRECTIONS` for corrections, which
  is what puts them in corrections mode),
- the **rubric** and **solution** files,
- the **due date**, from Gradescope, converted to GMT,
- the **points graded by hand** -- the PDF assignment's total minus the rubric's
  (`points_awarded_elsewhere`; late penalties are charged on rubric + these).

It shows the entries, writes them on your say-so, and warns about anything that would
stop the watcher from starting. The first time, `config.watch.yaml` is created as a copy of
`config.yaml`, so your model, email and late-penalty settings carry over. A finished entry
looks like:

```yaml
assignments:
  - name: ME461_HW3
    rubric_file: ME461_HW3_rubric.yaml
    solution_file: data/solutions/ME461-HW3-SOLUTION.tex
    due_date: "2026-10-17 05:00:00"            # GMT (2026-10-16 22:00 America/Los_Angeles)
    gradescope: {tex: 7654321, pdf: 7654320}
    grading: {late_penalties: {points_awarded_elsewhere: 6}}

watch:
  source: gradescope
  gradescope:
    course: 1234567
  notify_email: you@university.edu
```

**Extensions: enter them on Gradescope** (the assignment's Extensions page, on the `.tex`
or the PDF assignment -- students see them there too). LaTA reads them on every check;
if a student has one on both, the later date counts. An extension moves that student's
late-penalty clock and missing-file notices; it does not delay grading -- a student who
has submitted is graded with the class at the assignment's due date. If they resubmit
within their extension after being graded, you are told (it is not regraded
automatically). Without Gradescope (the inbox source), or for an extension Gradescope
stores as something other than a fixed date, list it in
`data/input/<NAME>/extensions.yaml` instead (`Jane Doe: 24` for 24 hours); both are used,
and the larger counts.

**Check before you rely on it:**

```bash
uv run grade.py --config config.watch.yaml --dry-run
```

A dry run fetches from Gradescope and reports what it would grade, enter (and how that
compares with any grades already on Gradescope), notify and send -- and does none of it.

---

## 5. Running the watcher

```bash
caffeinate -i uv run grade.py --config config.watch.yaml
```

`caffeinate -i` keeps the Mac awake. You can start it any time; before a deadline it only
watches. At startup it checks every rubric and solution exists and logs in to email.

What happens, per student:

| When | What LaTA does |
|---|---|
| Before the assignment's due date | Nothing but watch (extensions do not delay grading) |
| Deadline passed, `.tex` and PDF in, no resubmission for 15 minutes | Grades them, writes the report, enters the grade on Gradescope |
| Deadline passed, only the `.tex` | An hour after the `.tex`: emails "PDF missing -- late penalty accruing". Graded when the PDF comes |
| Deadline passed, only the PDF | An hour after the PDF: emails ".tex missing", and enters a zero on Gradescope. Graded (replacing the zero) when the `.tex` comes |

So the on-time class is graded as one batch just after the deadline, and late students one
by one as they arrive. While grading, the terminal shows a live dashboard: each slot's
student and problem, progress, problems per hour, ETA, and one slot's reasoning as it is
written. Between checks it prints a status line per assignment:

```
   ME461_HW3: 27 graded, 27 on Gradescope, 0 sent, 0 held, 27 ready to send | waiting: 2 waiting for PDF
```

and accepts commands (below).

---

## 6. Releasing feedback

When students' grades are on Gradescope, LaTA tells you (console, and email to
`notify_email`): *"feedback for 27 student(s) is ready."* Then:

1. **Grade the hand-graded questions** on Gradescope.
2. **Publish** the grades on Gradescope.
3. **Type `send ME461_HW3`** into the watcher (or just `send`, for everything ready).

`send` checks that the assignment is published and that every waiting student is fully
graded on Gradescope -- if not, it lists who is not and stops (`send ME461_HW3 --force`
sends anyway). Then it **posts the grades to Canvas** and **emails each student their
report**. Students graded later wait for your next `send`.

### Releasing automatically

Set `watch.auto_send_feedback: true` (or run with `--send`) and the watcher does what `send`
would, by itself, per student, whenever the checks pass:

- nothing goes out until you have **published once** -- LaTA tells you so, and the first
  batch is released on the next check after you publish;
- after that, each student goes out as soon as they are **fully graded on Gradescope**:
  straight away if nothing is graded by hand, otherwise once a TA has graded their
  hand-graded questions;
- every release **posts to Canvas first**, then emails.

So for stragglers you do nothing (or only the hand grading): once an assignment is
published, Gradescope shows students new grades as they are entered, and LaTA syncs and
emails them. Students graded before the watcher started are never released automatically
(use `send`), and held reports and grade conflicts wait for you in either mode.

---

## 7. Late submissions, missing files and resubmissions

- **Late submissions** are graded like any other, with the late penalty from the later of
  the `.tex` and PDF times (any fraction of a day counts as a day), charged on the rubric
  total plus the hand-graded points.
- **Incomplete submissions** get one email each, as in the table above, an hour after
  the student's last upload -- so a student who uploads the PDF a few minutes after the
  `.tex` is never warned.
- **A new `.tex` after grading** is not regraded: it is almost always a mistake, so LaTA
  flags it to you and leaves the grade alone. Handle any exception by hand.
- **Students graded before the watcher started** (by plain `grade.py`) are recognised
  from their reports and never graded twice. Their grades are entered on Gradescope like
  anyone's and they are included when you `send`, but never emailed automatically.

---

## 8. Corrections

Add the corrections `.tex` assignment like any other (its name ends in `_CORRECTIONS`).
After its deadline, each student's corrections are graded in corrections mode (original
extra credit and late penalty kept) and the grade is entered on their **resubmitted PDF**,
the new ungraded submission, so Gradescope keeps the first-pass grade in its history.

LaTA keeps track of who is where, which matters because corrections PDFs go to the same
PDF assignment as first passes:

- each student's first-pass submission time is **frozen when they are graded**, so a
  corrections PDF never adds a late penalty to the first pass;
- a student who submits the **first pass for the first time after corrections open** is
  graded as a (late) first pass, not mistaken for a correction;
- **corrections without a resubmitted PDF** are incomplete: they are still graded and
  sent, but the email says that, because the compiled PDF was not resubmitted, the
  corrections grade will not be applied -- and nothing is entered on Gradescope. If the
  PDF arrives afterwards, LaTA flags it to you.

---

## 9. When LaTA needs you

Anything LaTA will not decide on its own is emailed to `notify_email` (or shown as a
desktop notification) and written to `data/output/watch_log.txt`:

| Message | What to do |
|---|---|
| *graded but held: possible prompt injection* (or a segmentation warning, or a problem that failed to grade) | Type `held` to list held reports, and read the report (and its `debug/` files). If it is fine, `approve <name>`: the grade goes on Gradescope at once and the student is released with everyone else. If it is not, fix it and regrade (`grade.py --regrade`). A held student with *no report* cannot be approved -- regrade them |
| *already graded on Gradescope as 48; LaTA has 52. Left alone.* | Someone entered a different grade by hand. Resolve it on Gradescope; that student's feedback waits |
| *submitted a new .tex ... after being graded. Not regraded.* | Decide whether to regrade by hand |
| *resubmitted their PDF ... after corrections feedback* | Decide whether to apply the corrections grade |
| *feedback for N student(s) is ready* | Release it (section 6) |
| *Gradescope: ... session expired* | `uv run python -m lata.gradescope login`. Nothing is fetched or entered meanwhile |

LaTA cannot tell handwritten or otherwise invalid work apart from a real submission; look
through the submissions after feedback goes out.

---

## 10. Stopping, restarting and changing things

- **Stop** with Ctrl-C, at any time -- even mid-grading. Students whose reports were
  written are kept; the rest are graded on the next start, reusing every problem already
  graded.
- **Restart** with the same command. All state is in each assignment's
  `data/output/<NAME>_Feedback/watch_state.json`; keep it (in particular, frozen
  first-pass times cannot be recovered without it).
- **Add an assignment** with `setup_watch` and restart.
- **Change settings** (e.g. `points_awarded_elsewhere`) in the config and restart. Grades
  already on Gradescope are only ever revised from the score printed on the student's own
  report, never recalculated from changed settings.
- **Fix something on Gradescope** (a question's scoring, a missing rubric item): the
  watcher retries refused entries on every check, so no restart is needed.

---

## 11. Reference

### Commands

| Command | |
|---|---|
| `uv run python -m lata.gradescope login` / `check` | Sign in to Gradescope / test the saved session |
| `uv run python -m lata.setup_watch [--config FILE] [--list]` | Add Gradescope assignments to the config / just list them |
| `uv run grade.py --config FILE` | Watch (when the config has `assignments:`) |
| `... --dry-run` | One check that changes nothing and reports what would happen |
| `... --once` | One check, then exit |
| `... --send` / `--no-send` | Release automatically / wait for `send` (default) |
| `... --concurrency N`, `--watch-slot N`, `--no-dashboard` | Slots, which slot's reasoning to show, plain log lines |
| `uv run python tests/run_all.py` | Run the test suite (made-up students) |

Typed into the running watcher: `send [ASSIGNMENT] [--force]`, `held` (list held reports),
`approve <STUDENT> [ASSIGNMENT]` (release a held report after review), `status`, `check`
(check for submissions now), `help`.

### Settings (`watch:`)

| Setting | Default | |
|---|---|---|
| `source` | `inbox` | `gradescope` to fetch and enter grades; `inbox` reads `.zip` exports from `inbox_dir/<NAME>/` |
| `gradescope.course` | -- | the course's Gradescope id |
| `gradescope.enter_grades` | `true` | enter LaTA's grades on Gradescope |
| `gradescope.canvas_sync` | `true` | post grades to Canvas on `send` |
| `auto_send_feedback` | `false` | release automatically whenever `send` would succeed (published, fully graded), per student |
| `send_notices` | `true` | "incomplete submission" emails |
| `notify_email` | -- | where "needs your attention" emails go |
| `poll_minutes` | `10` | how often to check |
| `settle_minutes` | `15` | quiet time after a student's last upload before grading |
| `notice_after_hours` | `1` | wait before an "incomplete submission" email |
| `unload_model_after_idle_minutes` | `120` | stop the model server when idle |

Per assignment: `name`, `rubric_file`, `solution_file`, `due_date` (GMT),
`gradescope: {tex, pdf, question}`, `watch_until`, and any `grading:` or `paths:`
override (e.g. `grading: {late_penalties: {points_awarded_elsewhere: 6}}`).

### Files

| File | |
|---|---|
| `data/output/<NAME>_Feedback/Feedback_<Student>.pdf` | the reports |
| `data/output/<NAME>_Feedback/watch_state.json` | what LaTA knows about each student |
| `data/output/<NAME>_Feedback/grading_metadata.yaml` | scores, late penalties, extra credit per report |
| `data/output/<NAME>_Feedback/email_delivery.yaml` | who has been emailed what |
| `data/output/<NAME>_Feedback/debug/` | prompts, responses and reasoning per student and problem |
| `data/input/<NAME>/extensions.gradescope.yaml` | extensions read from Gradescope (rewritten every check) |
| `data/output/watch_log.txt` | everything the watcher did |
| `~/.lata/gradescope-profile/session.json` | your Gradescope session |

---

## 12. Troubleshooting

**"Not logged in to Gradescope"** -- run `uv run python -m lata.gradescope login`.

**Grades are not appearing on Gradescope** -- check the watcher's messages: a grade
entered by hand that differs is left alone; a question LaTA cannot find needs
`gradescope: {..., question: "HW3"}`. `status` shows how many are on Gradescope.

**`send` refuses** -- it says why: not published, or students not fully graded
(hand-graded questions). Fix that, or use `--force`.

**The watcher will not start** -- it lists what is missing: a rubric or solution file, a
due date, or a corrections assignment whose first pass is neither listed nor graded.

**Grading is slow or the model will not load** -- see `data/output/llama_server.log`.
Close other large models; the reference model needs about 110 GB.

**A student got the wrong late penalty** -- penalties come from the later of the `.tex`
and PDF times, minus any extension in `extensions.yaml`; check `grading_metadata.yaml`.
For corrections, the original penalty is reused.

**Something else** -- every action is in `data/output/watch_log.txt`, and every grading
request's prompt, response and reasoning is under the assignment's `debug/` folder.
