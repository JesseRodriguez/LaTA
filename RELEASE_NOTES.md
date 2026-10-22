# Release Notes

## v2.0.2

- **New:** extensions are read from Gradescope. Enter them on the assignment's Extensions
  page (the `.tex` or the PDF assignment -- students see them there); the watcher picks them
  up on every check, so `extensions.yaml` is no longer needed with the Gradescope source.
  It still works, and both are combined.
- **Changed:** students with an extension are graded at the assignment's due date along with
  everyone else, instead of waiting until their extended deadline. The extension still sets
  their late penalty and when a missing-file notice is due. A resubmission inside their
  extension, after grading, is flagged as such rather than regraded.
- **Fixed:** scores on feedback reports are rounded to at most two decimals, without
  trailing zeros -- a 0.1 deduction showed as `0.0999999996`, and late penalties could show
  similar floating-point noise.

## v2.0.1

- **Fixed:** students who submitted a PDF but no `.tex` now get your full-deduction rubric
  item (e.g. "No .tex file") on Gradescope. Gradescope stores a negatively scored
  question's deductions as positive weights, so the item was never found and LaTA entered
  an equivalent point adjustment instead -- the score was already right (0), only the
  rubric item was missing.

## v2.0.0 -- From deadline to feedback, unattended

LaTA used to be something you ran after exporting an assignment by hand. v2.0.0 can run
for a whole term: it watches your Gradescope assignments, grades each submission once it
is due and complete, enters the grade on Gradescope, keeps students informed when their
submission is incomplete, and sends feedback when you release it -- late submissions and
corrections included. Grading itself is several times faster, with a live dashboard.

The [User Guide](docs/USER_GUIDE.md) covers the new workflow end to end.

> **Reference setup:** Mac Studio M3 Ultra, 256 GB; Qwen3.8-Flash-Next (Unsloth GGUF
> UD-Q4_K_XL) on llama.cpp with 4 slots, `reasoning_effort: low`, temperature 0.1,
> top_p 0.9, presence penalty 0.2 -- about **40 problems per hour**. Details in the
> [README](README.md).

### New

**The watcher** (`uv run grade.py` with an `assignments:` list in the config)
- Grades each student once the deadline (plus any extension) has passed, both the `.tex`
  and the PDF are in, and they have stopped resubmitting for 15 minutes; late students
  one by one as they arrive. Runs until you press Ctrl-C.
- Keeps a per-student record across the `.tex` assignment, the PDF assignment and
  corrections. First-pass submission times are frozen when graded, so a corrections PDF
  never adds a late penalty, and a first pass submitted after corrections open is graded
  as a late first pass instead of being mistaken for a correction.
- Holds back reports that need a person (a problem failed to grade, problems could not be
  found, possible prompt injection, no report) and tells you.
- Flags what it will not decide on its own: a new `.tex` after grading (never regraded
  automatically), a corrections PDF that arrives after feedback.
- `--dry-run` reports what one check would do -- grade, enter, notify, send -- and does
  none of it. Commands can be typed into the running watcher: `send`, `status`, `check`.
- Stopping it is safe at any time: students cut off mid-grading are picked up on the next
  start, reusing every problem already graded.

**Gradescope integration** (`watch.source: gradescope`)
- **Passive fetch**: each check reads the `.tex` and PDF assignments' grade lists and
  downloads the `.tex` export only when something changed. PDF submission times come from
  the grade list, so PDFs are never downloaded and the PDF export step is gone.
- **Grade entry**: each grade goes into the LaTA question's point-adjustment field as you
  would enter it -- points off on a negatively scored question (the "Correct" rubric item
  for a perfect score), the score on a positively scored one, a full deduction for a PDF
  without a `.tex`. Only ungraded submissions (or LaTA's own entries) are written, and each
  save is read back; a different grade entered by hand is reported, never overwritten.
  Corrections go on the student's resubmitted PDF, keeping the first-pass grade in
  Gradescope's history.
- **Release on `send`**: feedback waits until you have graded hand-graded questions and
  published. `send` checks both, posts the grades to Canvas, and emails the reports.
- **Or release automatically** (`auto_send_feedback: true`): after you publish once, each
  student is synced to Canvas and emailed as soon as they are fully graded on Gradescope --
  immediately with nothing graded by hand, otherwise once their hand-graded questions are
  done. Stragglers need nothing from you.
- Signs in through a real browser once (`python -m lata.gradescope login`, SSO works);
  the session is saved privately and LaTA never sees your password.
- **`python -m lata.setup_watch`** adds assignments to the config straight from
  Gradescope: it pairs each `.tex` assignment with its PDF assignment (corrections with the
  original homework's), fills in IDs and the due date (converted to GMT), suggests the
  rubric, solution and hand-graded points, and warns about anything that would stop the
  watcher.

**Students are told when a submission is incomplete**
- PDF but no `.tex`: an email that the work cannot be graded and late penalties accrue
  until the `.tex` is submitted (also from `post_grading.py send-feedback`).
- `.tex` but no PDF: *"ACTION REQUIRED ... PDF missing -- late penalty accruing"*.
- Corrections without a resubmitted PDF: the feedback email says the corrections grade will
  not be applied (also in `send-feedback`, which lists those students before sending).
- Each notice is sent once, an hour after the student's upload and never before the
  deadline. All wording is configurable.

**Parallel grading and the dashboard**
- `grading.concurrency: N` grades N problems at once against a multi-slot model server.
- A live terminal dashboard shows each slot's student and problem, progress, problems per
  hour, ETA, recent events, and one slot's reasoning as it is written (`--watch-slot`);
  `--no-dashboard` prints plain log lines.
- Grading resumes: each finished problem is saved, and a restarted run reuses it when
  nothing that determines the grade has changed.

**Models**
- **Name a model, and it is served**: `grader.model: "Qwen"` finds a GGUF file (in
  `system.model_dirs`) or an Ollama model, starts `llama-server` with one slot per worker
  and the settings that architecture needs, and stops it afterwards (in the watcher: after
  two idle hours).
- **Any OpenAI-compatible server** (llama.cpp, vLLM, LM Studio) as a grading backend,
  with reasoning read from `reasoning_content`.

**Smaller things**
- Gradescope `.zip` exports can be dropped in `data/input/inbox/` unrenamed and unzipped;
  re-exports merge, with a student's newer submission replacing the older one.
- `grading_metadata.yaml` records each report's total, maximum, late deduction and final
  score.
- A test suite with made-up students: `uv run python tests/run_all.py`.

### Fixed

- The score validator had silently never run; it is replaced by one that checks every
  graded item against the rubric (maximum points, scores within range) before totals are
  computed.
- JSON sanitizing corrupted LaTeX beginning with `\b`, `\f`, `\n`, `\r` or `\t`
  (`\frac`, `\nabla`, `\theta`...) in grader output.
- The LaTeX-fix retry overwrote a resubmission's report, read a stale log, and dropped the
  student's extension.
- `--regrade` did not record the late penalty shown on the report.
- The audit flagged criterion names reused across rubric sections.
- LaTA's modules were loaded under two names (`src.lata` and `lata`), so some errors --
  notably cancelling a run -- were not caught where they should have been.

### Upgrading from v1.0.0

- Existing configs keep working. To use the new features:
  - `grader.model: "<name>"` instead of `model_name` + a server address, if you want LaTA
    to serve a GGUF model; `grading.concurrency: 4` for parallel grading. With an Ollama
    grader and `concurrency > 1`, set `OLLAMA_NUM_PARALLEL` (see Getting Started).
  - For the watcher, an `assignments:` list and a `watch:` section -- easiest with
    `python -m lata.setup_watch`. Keep it in a separate file (`config.watch.yaml`, which is
    git-ignored) if you also grade single assignments.
  - For Gradescope: `uv sync --extra gradescope`, `uv run playwright install chromium`, then
    `uv run python -m lata.gradescope login`.
- **Behaviour change:** when the config has an `assignments:` list, `grade.py` runs the
  watcher instead of grading a single assignment.
- Do not use your OpenAI-compatible server with Ollama's `repeat_penalty: 1.4`: on
  llama.cpp it breaks the JSON output (LaTA's served models use their own settings).

### Known limitations

- Gradescope has no public API for instructors; LaTA uses the same requests as its web
  pages, which Gradescope could change.
- LaTA cannot tell handwritten or invalid work from a real submission: review submissions
  after feedback goes out.
- Group submissions are graded and emailed for the first submitter only.
- Tested on macOS with Apple Silicon.

---

## v1.0.0 -- Initial public release

Gradescope export ingestion, LLM segmentation and grading against a rubric, LaTeX
feedback reports with late penalties and extra credit, a corrections workflow,
post-grading tools (audit, TA splits, email), and a companion Gradescope autograder.
