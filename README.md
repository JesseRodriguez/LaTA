# LaTA: Large Language Teaching Assistant

LaTA grades LaTeX-based STEM homework with a local open-source LLM. It reads student
submissions from Gradescope, grades each problem against your reference solution and
rubric, and produces an individual PDF feedback report per student. Left running, it
fetches submissions from Gradescope as they arrive, grades them once they are due,
enters the grades on Gradescope, and emails the feedback when you release it.

> [!NOTE]
> **Reference setup — what LaTA is developed and run on in production (v2.0.0, Fall 2026)**
>
> | | |
> |---|---|
> | **Hardware** | Apple Mac Studio, M3 Ultra, **256 GB** unified memory (macOS 26) |
> | **Grading model** | **Qwen3.8-Flash-Next**, Unsloth GGUF **UD-Q4_K_XL** ([`unsloth/Qwen3.8-Flash-Next-GGUF`](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF), ~104 GB, plus `mmproj-F16`), served by llama.cpp's `llama-server`, which LaTA starts itself (`grader.model: "Qwen"`) |
> | **Model settings** | 4 slots (`grading.concurrency: 4`), 32k context per slot, `reasoning_effort: low`, temperature 0.1, top_p 0.9, presence_penalty 0.2, max_tokens 16384, **no** repeat penalty, **no** reasoning budget, flash attention auto — all applied automatically for this model |
> | **Segmentation model** | `gpt-oss:20b` via Ollama (only needed when a submission's problems cannot be found by pattern) |
> | **Typical throughput** | **about 40 problems per hour** with 4 slots — roughly 13 students an hour on a three-problem assignment |
>
> Other hardware and models work (see [Getting Started](docs/GETTING_STARTED.md)), but this is
> the configuration that has graded full classes end to end. Smaller machines need smaller
> models; LaTA has only been tested on Apple Silicon.

```
LaTA Grading System - Automated grading workflow using open-source LLMs.
Copyright (C) 2026 Jesse A. Rodriguez

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
```

**New in v2.0.0:** a watcher that runs unattended from deadline to feedback, Gradescope
integration (fetching submissions, entering grades, Canvas sync), parallel grading with a
live dashboard, and models served automatically. See the [release notes](RELEASE_NOTES.md)
and the [User Guide](docs/USER_GUIDE.md).

---

## How It Works

Each submission goes through four stages:

1. **Ingest** -- Reads the Gradescope export: student metadata, the LaTeX source, and the submission times of both the `.tex` and the PDF assignment (late penalties use the later of the two).
2. **Segment** -- Splits each student's `.tex` file into problems, by pattern (`\Prob{N}`, `\section{...}`) or, failing that, with a small LLM.
3. **Grade** -- A reasoning LLM grades each problem against the reference solution and rubric, returning structured JSON: a score per rubric item, an internal audit note for the instructor, and a Socratic hint for the student. Scores are checked against the rubric before they are used.
4. **Report** -- Results are rendered into a LaTeX feedback report and compiled to PDF, with late penalties and extra credit applied.

Around that pipeline, LaTA can run in two ways:

- **Watching** (new) -- List a term's assignments and leave `grade.py` running. It fetches submissions from Gradescope, grades each student once the deadline has passed and both files are in, enters the grades on Gradescope, emails students whose submission is incomplete, and sends feedback when you release it. Late submissions and corrections are handled the same way, until you stop it.
- **One assignment at a time** -- Export from Gradescope, drop the `.zip` files in a folder, run `grade.py`, then send feedback with `post_grading.py`.

---

## Key Features

- **Unattended watching** -- Grades submissions as they arrive, after the deadline, until you stop it; tracks every student across the `.tex` assignment, the PDF assignment and corrections, so late first passes and incomplete submissions are not missed.
- **Gradescope integration** -- Fetches submissions (only when something changed), enters each grade in the point-adjustment field the way you would, checks the grades are published and complete, and posts them to Canvas when you release feedback. Uses a saved login (SSO works); no password is stored.
- **You choose how feedback is released** -- By default it waits until you have graded any hand-graded questions and published; then one `send` command posts to Canvas and emails everyone ready. Or let LaTA release each student itself, Canvas sync included, as soon as they are fully graded on a published assignment.
- **Missing-file notices** -- Students who submit only the `.tex` or only the PDF are emailed (once) that their submission is incomplete and late penalties are accruing; corrections without a resubmitted PDF are told the corrections grade will not be applied.
- **Parallel grading with a live dashboard** -- Several problems at once against a multi-slot model server, with per-slot progress, throughput, ETA and live reasoning. Stopped runs resume without regrading finished problems.
- **Name a model, and it is served** -- `grader.model: "Qwen"` finds the GGUF file or Ollama model, starts `llama-server` with settings for that model's architecture, and stops it afterwards.
- **Fully local inference** -- All LLM calls go to a model server on your machine. No student work leaves it.
- **Any OpenAI-compatible server** -- llama.cpp, vLLM or LM Studio, as well as Ollama.
- **Chunk-based grading with dual feedback** -- Each problem graded independently; internal audit notes for the instructor, Socratic hints for the student.
- **Safety checks** -- Score validation against the rubric, prompt-injection defenses and flags, reports held for review when grading failed or looks suspicious.
- **Late penalties, extensions and corrections** -- Per-day penalties from Gradescope timestamps (including hand-graded points), per-student extensions, and a corrections workflow that preserves original extra credit and late penalties.
- **Post-grading tools** -- Feedback auditing, TA workload splitting, and email (SMTP or macOS Mail.app).
- **Gradescope autograder** -- A companion validator that checks students' `.tex` submissions on Gradescope before they reach LaTA.

---

## Testing and Performance

LaTA has been used in production at Oregon State University: for ME 373 (Numerical Methods) in Winter 2026 -- 8 assignments, about 180 students each, graded with `gpt-oss:120b` -- and for ME 461/561 (Gas Dynamics) in Fall 2026 with the watcher and Gradescope integration, graded with Qwen3.8-Flash-Next (see the reference setup above).

| Model | Slots | Throughput (M3 Ultra) |
|---|---|---|
| Qwen3.8-Flash-Next UD-Q4_K_XL (llama.cpp) | 4 | about 40 problems/hour |
| `gpt-oss:120b` (Ollama) | 1 | about 1 problem/minute |
| `gpt-oss:120b` (Ollama) | 4 | about 1.5x the single-slot rate |

Other models (DeepSeek-R1:14b, QwQ:32b, Llama 3.1:8b) have been tested on smaller subsets. A test suite with made-up students covers the watcher's rules, the Gradescope workflow (against a fake Gradescope) and the grading pipeline: `uv run python tests/run_all.py`.

---

## Quick Start

### Prerequisites

- Python 3.14+ with [uv](https://docs.astral.sh/uv/)
- A LaTeX distribution with `pdflatex` (`brew install --cask mactex` on macOS, `texlive-full` on Linux)
- A model server: [llama.cpp](https://github.com/ggml-org/llama.cpp) (`llama-server`) for GGUF models, and/or [Ollama](https://ollama.com)
- For the Gradescope integration: the `gradescope` extra (Playwright and its browser)

### Installation

```bash
git clone https://github.com/JesseRodriguez/LaTA.git
cd LaTA
uv sync --extra gradescope                 # or just `uv sync` without Gradescope integration
uv run playwright install chromium         # only for the Gradescope integration
cp config.example.yaml config.yaml         # then set the grader model, email, late penalties
```

### Watching a term's assignments (recommended)

```bash
uv run python -m lata.gradescope login               # once: sign in to Gradescope in a browser window
uv run python -m lata.setup_watch --config config.watch.yaml   # pick assignments; IDs and due dates are filled in
uv run grade.py --config config.watch.yaml --dry-run # see what would happen, change nothing
caffeinate -i uv run grade.py --config config.watch.yaml        # leave it running
# ...when it says feedback is ready: grade hand-graded questions, publish, then type `send`
```

A new `config.watch.yaml` starts as a copy of your `config.yaml`, so model, email and
late-penalty settings carry over. The [User Guide](docs/USER_GUIDE.md) walks through it all.

### One assignment at a time

```bash
# 1. Download the Gradescope exports (.tex and PDF assignments) into data/input/inbox/
# 2. Put the rubric in data/rubrics/ and the solution in data/solutions/
#    (see data/rubrics/example_rubric.yaml for the format)
# 3. Set paths, assignment name and due date in config.yaml
uv run grade.py                              # grade (live dashboard with grading.concurrency > 1)
uv run grade.py --student "Jane Doe"         # or test on one student first
uv run python post_grading.py audit          # check for grading issues
uv run python post_grading.py send-feedback  # email feedback
```

Output appears in `data/output/{assignment_name}_Feedback/` with individual PDFs, LaTeX source logs, and debug files.

---

## Documentation

| Guide | Description |
|-------|-------------|
| [User Guide](docs/USER_GUIDE.md) | The watched workflow end to end: setup, each assignment, release, corrections, troubleshooting |
| [Release Notes](RELEASE_NOTES.md) | What is new in each version, and how to upgrade |
| [Getting Started](docs/GETTING_STARTED.md) | Detailed step-by-step setup, configuration reference, rubric writing guide, and troubleshooting |
| [Watching Assignments](docs/WATCHING.md) | Leave LaTA running: it grades submissions as they arrive, sends notices and feedback, and handles corrections |
| [Corrections Workflow](docs/CORRECTIONS_WORKFLOW.md) | How to re-grade student corrections while preserving original extra credit |
| [Post-Grading Tools](docs/POST_GRADING.md) | Audit, TA splits, email distribution, and late penalty adjustments |
| [Email Quick Start](docs/email/EMAIL_QUICK_START.md) | Setting up email delivery of feedback PDFs |
| [Email Safety Guide](docs/email/EMAIL_SAFETY_GUIDE.md) | Pre-send verification and safety procedures |
| [AppleScript Email](docs/email/APPLESCRIPT_EMAIL_GUIDE.md) | macOS Mail.app integration via AppleScript |
| [Gradescope Autograder](gradescope_autograder/README.md) | Companion submission validator for Gradescope |

---

## Project Structure

```
LaTA/
├── grade.py                    # Main entry point for grading
├── post_grading.py             # Post-grading tools (audit, TA splits, email)
├── config.example.yaml         # Configuration template
├── RELEASE_NOTES.md            # What changed in each version
├── src/lata/                   # Core library
│   ├── ingest.py               #   Gradescope export parsing
│   ├── inbox.py                #   Unpacks Gradescope .zip exports
│   ├── pipeline.py             #   Grades one assignment (what grade.py runs)
│   ├── watch.py                #   Watches a list of assignments until stopped
│   ├── ledger.py               #   Per-student state and the watcher's rules
│   ├── gradescope.py           #   Gradescope session (login, exports, grades, Canvas)
│   ├── gradescope_sync.py      #   Fetching from and entering grades on Gradescope
│   ├── setup_watch.py          #   Adds Gradescope assignments to the watch config
│   ├── scheduler.py            #   Parallel grading and the live dashboard
│   ├── serving.py              #   Finds a named model and serves it (llama-server)
│   ├── segmenter.py            #   Problem extraction via LLM
│   ├── grader.py               #   LLM-based rubric evaluation
│   ├── reporter.py             #   PDF generation and error recovery
│   ├── emailer.py              #   SMTP email backend
│   ├── applescript_mailer.py   #   macOS Mail.app backend
│   └── validate_and_fix_scores.py  # Score consistency checks
├── tests/                      # Made-up-student tests: uv run python tests/run_all.py
├── templates/
│   └── feedback_report.tex     # Jinja2 LaTeX template for feedback PDFs
├── gradescope_autograder/      # Companion Gradescope validator
├── data/
│   ├── input/                  # Gradescope exports (git-ignored)
│   ├── output/                 # Generated feedback (git-ignored)
│   ├── rubrics/                # YAML rubric files (git-ignored except example)
│   └── solutions/              # Reference solutions (git-ignored)
└── docs/                       # Detailed documentation
```

---

## Security and Privacy

- **All inference is local.** Student work is graded by a model server on your machine (llama.cpp or Ollama on localhost) and is never sent to an LLM service.
- **Gradescope access is yours, and limited.** The Gradescope integration talks only to gradescope.com, as you, through a session you sign in to yourself; LaTA never sees your password. The session is stored in `~/.lata/gradescope-profile/session.json`, readable only by you. LaTA's writes are limited to saving the LaTA question's grade and posting grades to Canvas when you `send`; it never publishes, changes settings or deletes anything.
- **Student data is git-ignored.** The `data/` directories containing submissions, output, and solutions are excluded from version control by default, as are `config.yaml` and other per-course `config.*.yaml` files.
- **Prompt injection defenses** include security preambles, strong input delimiters, role reinforcement after student text, post-grading keyword scanning, and full debug transparency. See the [Getting Started guide](docs/GETTING_STARTED.md) for details.
- **Anonymization** is available via `anonymize: true` in the configuration.

---

## Contributing

Contributions are welcome. Areas of interest:

- Support for additional LLM backends (vLLM, llama.cpp server, cloud APIs)
- Non-LaTeX submission formats (Markdown, Jupyter notebooks)
- Web interface for rubric creation and grading review
- Automated rubric generation from solution files
- Cross-platform testing (Linux, Windows)

---

## Acknowledgments

- Built on [Ollama](https://ollama.ai) for local LLM inference
- Uses [Jinja2](https://jinja.palletsprojects.com/) for LaTeX templating
- Inspired by the challenge of grading 180+ engineering assignments per week with meaningful, individualized feedback

---

## License

LaTA is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later).
