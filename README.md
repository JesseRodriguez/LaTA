# LaTA: Large Language Teaching Assistant

LaTA is an automated grading pipeline for LaTeX-based STEM assignments. It ingests bulk submissions from Gradescope, grades them against a reference solution and rubric using a local open-source LLM (via Ollama), and generates individualized PDF feedback reports for each student.

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

---

## How It Works

LaTA processes assignments through a four-stage pipeline:

1. **Ingest** -- Parses a Gradescope export directory, extracts student metadata and LaTeX source files, and sanitizes content.
2. **Segment** -- A fast LLM (e.g., `gpt-oss:20b`) extracts individual problem sections from each student's monolithic `.tex` file so they can be graded independently.
3. **Grade** -- A reasoning LLM (e.g., `gpt-oss:120b`) evaluates each problem section against the reference solution and rubric, producing structured JSON with scores, internal audit notes, and student-facing Socratic hints.
4. **Report** -- Results are rendered into a LaTeX feedback template, compiled to PDF, and organized per student. Late penalties, extra credit, and score validation are applied automatically.

After grading, post-processing tools handle TA workload splitting, feedback auditing, email distribution, and a full corrections workflow for resubmissions.

---

## Key Features

- **Fully local inference** -- All LLM calls go to a local model server. No student data leaves your machine.
- **Name a model, and it is served** -- Point `grader.model` at an Ollama model or a GGUF file; GGUF models get their own `llama-server` started and stopped around the run, configured from the architecture in the file.
- **Reasoning model support** -- Native support for `<think>`-tag models (DeepSeek-R1, QwQ, GPT-OSS) with automatic chain-of-thought extraction.
- **Chunk-based grading** -- Each problem is graded independently for better accuracy and debuggability.
- **Dual feedback** -- Internal instructor audit notes (what the LLM actually saw) and student-facing Socratic hints (guiding without giving answers).
- **Automatic error recovery** -- JSON schema validation with retry, LLM-based LaTeX compilation error fixing, and comprehensive failure logging.
- **Prompt injection defense** -- Five-layer security system against students attempting to manipulate grades via embedded instructions.
- **Late penalty engine** -- Configurable per-day penalties calculated from Gradescope timestamps, displayed on feedback PDFs.
- **Corrections workflow** -- Re-grade student corrections against the same rubric while preserving original extra credit scores.
- **Post-grading tools** -- Automated feedback auditing, TA workload splitting, and email distribution (SMTP or macOS Mail.app via AppleScript).
- **Incremental grading** -- Skip previously graded students; grade new and late submissions without redoing work.
- **Parallel grading** -- Grade several problems at once against a multi-slot LLM server, with a live terminal dashboard (per-slot progress, throughput, ETA, live reasoning). Stopped runs resume without regrading finished problems.
- **Gradescope autograder** -- Companion validator that runs on Gradescope to check student `.tex` submissions for structural issues before they reach LaTA.

---

## Testing and Performance

LaTA has been developed and tested at scale in production for the ME 373 (Numerical Methods) course at Oregon State University during Winter 2026. The only large-scale testing environment so far has been:

- **Hardware**: Apple Mac Studio, M3 Ultra, 256 GB unified memory
- **Grading model**: `gpt-oss:120b` (120-billion-parameter open-source reasoning model, served via Ollama)
- **Segmentation model**: `gpt-oss:20b`
- **Throughput**: Approximately one problem per minute (wall-clock, including LLM inference, LaTeX compilation, and error recovery) grading one problem at a time; about 1.5x faster with `concurrency: 4` and `OLLAMA_NUM_PARALLEL=4` (see [Getting Started](docs/GETTING_STARTED.md#parallel-grading))
- **Scale**: 8 homework assignments, approximately 180 students each, with 2--6 problems per assignment

Other models (DeepSeek-R1:14b, QwQ:32b, Llama 3.1:8b) have been tested on smaller subsets and work with LaTA, but `gpt-oss:120b` is the only model that has been validated end-to-end at full class scale.

---

## Quick Start

### Prerequisites

- Python 3.14+ with [uv](https://docs.astral.sh/uv/)
- A LaTeX distribution with `pdflatex` (`brew install mactex` on macOS, `texlive-full` on Linux)
- [Ollama](https://ollama.ai) running locally with your chosen models pulled

### Installation

```bash
git clone https://github.com/jarodz/LaTA.git
cd LaTA
uv sync
```

### Five-Step Workflow

```bash
# 1. Download the Gradescope export(s) and drop the .zip files in data/input/inbox/
#    (no unzipping or renaming; grade.py unpacks them, and re-exports merge)

# 2. Create your rubric YAML and reference solution
#    See data/rubrics/example_rubric.yaml for the format

# 3. Configure LaTA
cp config.example.yaml config.yaml
#    Edit config.yaml: set paths, model names, due date, subject area

# 4. Grade
uv run python grade.py                          # Grade all students
uv run python grade.py --student "Jane Doe"     # Test on one student first
uv run python grade.py --concurrency 4          # Grade 4 problems at a time (live dashboard)

# 5. Post-process
uv run python post_grading.py audit             # Check for grading issues
uv run python post_grading.py split-for-tas 3   # Split feedback for 3 TAs
uv run python post_grading.py send-feedback      # Email feedback to students
```

Or list several assignments in `config.yaml` and `uv run grade.py` keeps watching them:
it fetches submissions from Gradescope, grades them as they arrive (late ones included),
enters the grades on Gradescope, and sends feedback when you release it (see [Watching
Assignments](docs/WATCHING.md)).

Output appears in `data/output/{assignment_name}_Feedback/` with individual PDFs, LaTeX source logs, and debug files.

---

## Documentation

| Guide | Description |
|-------|-------------|
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
├── src/lata/                   # Core library
│   ├── ingest.py               #   Gradescope export parsing
│   ├── inbox.py                #   Unpacks Gradescope .zip exports
│   ├── pipeline.py             #   Grades one assignment (what grade.py runs)
│   ├── watch.py                #   Watches a list of assignments until stopped
│   ├── ledger.py               #   Per-student state and the watcher's rules
│   ├── gradescope.py           #   Gradescope session (login, exports, grades, Canvas)
│   ├── gradescope_sync.py      #   Fetching from and entering grades on Gradescope
│   ├── segmenter.py            #   Problem extraction via LLM
│   ├── grader.py               #   LLM-based rubric evaluation
│   ├── reporter.py             #   PDF generation and error recovery
│   ├── emailer.py              #   SMTP email backend
│   ├── applescript_mailer.py   #   macOS Mail.app backend
│   └── validate_and_fix_scores.py  # Score consistency checks
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

- **All inference is local.** Student submissions never leave your machine. Ollama runs entirely on localhost.
- **Student data is git-ignored.** The `data/` directories containing submissions, output, and solutions are excluded from version control by default.
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
