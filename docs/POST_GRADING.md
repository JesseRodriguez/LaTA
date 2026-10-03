# Post-Grading Workflow Guide

After LaTA finishes grading and generating feedback PDFs, the `post_grading.py` script
provides four subcommands for distributing results, auditing quality, adjusting late
penalties, and emailing students.

```
python post_grading.py <subcommand> [options]
```

Available subcommands:

| Subcommand                | Purpose                                                    |
|---------------------------|------------------------------------------------------------|
| `split-for-tas`           | Split feedback first-pages into per-TA PDFs for Gradescope |
| `audit`                   | Check feedback reports for LLM-introduced errors           |
| `finalize-late-penalties` | Recalculate late penalties using PDF submission timestamps  |
| `send-feedback`           | Email feedback PDFs to students                            |

All subcommands accept `--config <path>` (default: `config.yaml`).

---

## 1. TA Splits (`split-for-tas`)

### Purpose

After grading, TAs need to enter scores into Gradescope manually. This subcommand
creates one PDF per TA containing only the **first page** (summary page) of each
student's feedback report. TAs open their assigned PDF side-by-side with Gradescope
and enter scores in order.

### How It Works

1. **Loads submission metadata** from the Gradescope export directory.
2. **Determines ordering.** If `pdf_submission_metadata.yml` exists in the export
   directory, submissions are sorted by PDF submission timestamp (earliest first).
   Otherwise, TeX submission timestamps are used. This matters because Gradescope
   presents submissions in the order students submitted their PDFs, so the TA split
   PDFs must match that order.
3. **De-duplicates versioned reports.** If incremental grading produced both
   `Feedback_Jane_Doe.pdf` and `Feedback_Jane_Doe_v2.pdf`, only the latest version
   is included.
4. **Splits evenly.** Submissions are divided among TAs as equally as possible.
   When the count does not divide evenly, the first TAs each receive one extra
   submission.
5. **Extracts the first page** of each feedback PDF and concatenates them into a
   single PDF per TA.
6. **Generates a text index file** (`TA_Assignment_Index.txt`) listing each TA's
   assigned students with names, SIDs, and submission timestamps.

### Placeholder Pages for Missing Feedback

When PDF submission metadata is available, some students may have submitted a PDF to
Gradescope but never submitted a TeX file (so no automated feedback exists). In
normal mode, a placeholder page is inserted for these students. The placeholder
instructs the TA to enter 0 points and move on. This keeps the page count aligned
with the Gradescope submission list so TAs can work through both in parallel.

### Corrections Mode

When the assignment name ends with `_CORRECTIONS`, the subcommand enters corrections
mode. In this mode, placeholder pages are **not** generated -- only students who
actually submitted a TeX file for the corrections round are included. This avoids
cluttering the TA PDF with students who did not participate in corrections.

### Output

Files are written to `data/output/<AssignmentName>_Feedback/TA_Splits/`:

- `TA1_Gradescope_Entry_<AssignmentName>.pdf`
- `TA2_Gradescope_Entry_<AssignmentName>.pdf`
- ...
- `TA_Assignment_Index.txt`

### CLI Usage

```
python post_grading.py split-for-tas [--num-tas N] [--config PATH]
```

| Flag         | Default       | Description                          |
|--------------|---------------|--------------------------------------|
| `--num-tas`  | 3             | Number of TAs to split work among    |
| `--config`   | `config.yaml` | Path to configuration file           |

### Examples

```bash
# Split among 3 TAs (default)
python post_grading.py split-for-tas

# Split among 5 TAs
python post_grading.py split-for-tas --num-tas 5

# Use a non-default config
python post_grading.py split-for-tas --num-tas 4 --config config_hw2.yaml
```

---

## 2. Audit (`audit`)

### Purpose

LLMs can introduce subtle errors into feedback reports: fabricating rubric criterion
names, miscalculating point totals, or producing LaTeX that fails to compile. The
audit subcommand performs five automated checks across all feedback `.tex` files and
reports every issue it finds, along with ready-to-paste regrade commands.

### The 5 Checks

#### Check 1: LaTeX Compilation Failures

Looks for `.tex` files remaining in the main output directory (rather than only in
`logs/`). When PDF compilation succeeds, the `.tex` source is stored in `logs/` and
only the `.pdf` is left in the output directory. A `.tex` file in the output
directory means pdflatex failed and no PDF was produced.

#### Check 2: Rubric Item Name Mismatches

Parses every `\item \textbf{...}` entry from each feedback report and checks whether
the criterion name matches one defined in the rubric YAML. Three outcomes are
possible:

- **Exact match** -- no issue.
- **Prefix match with description bleed** -- the LLM appended part of the criterion
  description to the name (flagged if the name is more than 30% longer than the
  rubric entry).
- **No match at all** -- the LLM fabricated a criterion name that does not exist in
  the rubric.

LaTeX escaping (`\_`, `\&`, `\%`, etc.) is reversed before comparison.

#### Check 3: Point Total Validation

Verifies arithmetic consistency in the summary table at the top of each report:

- For each section row: `score` should equal `max - deducted`.
- The TOTAL row should equal the sum of individual section scores and max values.
- Section scores in the summary table should match the scores stated in the
  corresponding feedback subsection headers.

Pure extra-credit sections (where all items are marked `is_extra_credit` in the
rubric) are excluded from this check because their `max` is intentionally 0 while
their `score` reflects earned extra credit.

#### Check 4: Missing Feedback Sections

Every section defined in the rubric should appear as a row in the summary table. If
a section is absent, it is flagged. In corrections mode, pure extra-credit sections
are exempted because only corrected sections appear in a corrections report.

#### Check 5: Duplicate Criterion Names

Within each feedback section, criterion names should be unique. If the LLM listed
the same criterion twice (perhaps with different point values), it is flagged. The
same criterion name appearing in different problem sections is fine.

### Understanding False Positives

Some flags may not represent real errors:

- **Description bleed** can be benign if the LLM merely restated the criterion name
  with minor wording variation. The 30% length threshold catches most real cases but
  occasionally flags acceptable expansions.
- **Point mismatches of 0.01 or less** are filtered out (floating-point tolerance),
  but if the rubric uses fractional points, rounding differences can produce small
  mismatches that look alarming.
- **Missing sections** in corrections mode may be legitimate when only a subset of
  problems was re-graded.
- In corrections mode, "Original Extra Credit (preserved)" is a synthetic criterion
  used to carry forward extra-credit scores from the original grading run and is
  automatically excluded from the fabricated-criterion check.

### Regrade Commands Output

After listing all issues, the audit groups flagged students into two categories:

1. **Rubric/point issues** -- students with name mismatches, point errors, missing
   sections, or duplicates.
2. **Compilation-only failures** -- students whose only issue is a failed LaTeX
   build.

For each category, and for all flagged students combined, the audit prints a
ready-to-run `grade.py --regrade` command:

```
uv run python grade.py data/input/Assignment1_Export --regrade "Jane Doe" "John Smith"
```

A text file `audit_regrade_list.txt` is also saved to the output directory with the
full list and per-student issue annotations.

### CLI Usage

```
python post_grading.py audit [--config PATH]
```

| Flag       | Default       | Description                |
|------------|---------------|----------------------------|
| `--config` | `config.yaml` | Path to configuration file |

### Examples

```bash
# Run audit with default config
python post_grading.py audit

# Run audit against a different assignment config
python post_grading.py audit --config config_hw3.yaml
```

---

## 3. Send Feedback (`send-feedback`)

### Purpose

Emails each student their individual feedback PDF as an attachment. Tracks delivery
status so that re-running the command skips students who already received their
email.

### Email Configuration

Email settings live under the `email:` key in `config.yaml`. Key fields:

```yaml
email:
  smtp_server: "smtp.your-university.edu"
  smtp_port: 25
  use_ssl: false
  use_tls: true

  subject_template: "{assignment_name} has been graded, your feedback is ready"
  from_name: "LaTA Grading System"

  body_template: |
    Hello {student_name},

    Your submission for {assignment_name} has been graded.
    Please see the attached PDF for detailed feedback.

    Best regards,
    LaTA Grading System

  max_retries: 3
  retry_delay: 2        # exponential backoff: 2s, 4s, 8s
  timeout: 30
  batch_delay: 0.5      # delay between emails to avoid rate limiting
```

Template variables `{assignment_name}` and `{student_name}` are substituted at send
time. Credentials are never stored in the config file; you are prompted at runtime.

### Email Backends

LaTA supports two email backends, configured via `email.backend` in `config.yaml`:

- **`smtp`** (default) -- connects to the configured SMTP server. Supports SSL
  (port 465) and STARTTLS (port 25/587). Prompts for username and password at
  runtime.
- **`applescript`** -- macOS only. Sends emails through Apple Mail via AppleScript.
  No SMTP credentials are needed; Mail.app handles authentication. Configure the
  sending account with `email.applescript.from_account`.

If no `backend` key is present, SMTP is used. If `applescript` is selected but the
AppleScript module is unavailable, it falls back to SMTP automatically.

### Test Modes

**Dry-run mode** (`--dry-run`): walks through the entire send workflow -- loading
submissions, matching PDFs, rendering templates -- but never opens a network
connection or sends any email. Useful for verifying that all PDFs are found and
templates render correctly.

**Test recipients** (`--test-recipients`): sends real emails, but every email is
redirected to the specified address(es) instead of the actual students. Use this to
verify delivery and formatting before mailing the whole class.

### Delivery Tracking

Delivery status is saved to `data/output/<AssignmentName>_Feedback/email_delivery.yaml`.
On subsequent runs, students already marked as delivered are skipped unless
`--force-resend` is passed.

### Students Missing a .tex File

A student who submitted to the .pdf assignment but has no gradable .tex file -- they
never submitted to the .tex assignment, or submitted something other than a .tex file
to it -- is skipped by the grader and so has no feedback PDF. `send-feedback` finds
these students in `pdf_submission_metadata.yml` and sends them a notice (no
attachment) saying their .tex file is missing and should be submitted as soon as
possible to avoid a zero and further late penalties. They appear in the preview, in
their own section, before you confirm.

Notices are tracked under `missing_tex_notified` in `email_delivery.yaml`, so a later
run (for example after a late re-export) does not resend them; a student who has since
submitted the .tex file and been graded gets their feedback as usual. Override the
wording with `missing_tex_subject_template` and `missing_tex_body_template` under
`email:` (same `{assignment_name}` and `{student_name}` variables), or turn notices
off with `--no-missing-tex-notices`. They are not sent in corrections mode, where a
missing .tex file just means no corrections were submitted.

### Corrections Mode

When the assignment name ends with `_CORRECTIONS`, the emailer operates in
corrections mode. This is detected automatically and passed to the `FeedbackEmailer`
so that email subjects and body text can reflect the corrections context.

### CLI Usage

```
python post_grading.py send-feedback [OPTIONS]
```

| Flag                | Default       | Description                                        |
|---------------------|---------------|----------------------------------------------------|
| `--config`          | `config.yaml` | Path to configuration file                         |
| `--username`        | (prompted)    | SMTP username (skips interactive prompt)            |
| `--dry-run`         | off           | Simulate sending without actual emails             |
| `--test-recipients` | (none)        | One or more email addresses to redirect all mail to |
| `--force-resend`    | off           | Resend even if delivery was already recorded       |
| `--no-missing-tex-notices` | off    | Don't email students who submitted a PDF but no .tex file |

### Examples

```bash
# Dry run -- verify everything without sending
python post_grading.py send-feedback --dry-run

# Send to yourself first to check formatting
python post_grading.py send-feedback --test-recipients you@university.edu

# Send to two test addresses
python post_grading.py send-feedback --test-recipients you@university.edu ta@university.edu

# Send to all students for real
python post_grading.py send-feedback

# Force resend to everyone (ignores delivery tracking)
python post_grading.py send-feedback --force-resend

# Provide SMTP username non-interactively
python post_grading.py send-feedback --username jsmith
```

---

## 4. Late Penalty Adjustments (`finalize-late-penalties`)

### Purpose

LaTA calculates late penalties during grading using the TeX submission timestamp from
Gradescope. However, many courses require students to submit both a TeX source file
and a compiled PDF. A student might upload the TeX file on time but submit the PDF
late (or vice versa). The `finalize-late-penalties` subcommand recalculates penalties
using the **later** of the two timestamps, then regenerates affected feedback PDFs.

### How It Works

1. Loads TeX submission timestamps from the Gradescope export (via `GradescopeIngestor`).
2. Loads PDF submission timestamps from `pdf_submission_metadata.yml` in the export
   directory. This file must be generated separately (e.g., by `retroactive_metadata.py`).
3. For each student present in both datasets, compares the two timestamps.
4. If the PDF was submitted later than the TeX, the penalty is recalculated using the
   PDF timestamp.
5. Extracts the current score from the `.tex` log file by parsing the summary table
   (looks for "Points Before Penalty" or the TOTAL row).
6. Updates the `.tex` file: replaces or inserts the late-penalty warning box with the
   new days-late count, deduction amount, and final score.
7. Recompiles the PDF with `pdflatex` and moves it to the output directory.

### When to Run

Run this after grading is complete and before distributing results (before
`split-for-tas` or `send-feedback`). It only has an effect when
`pdf_submission_metadata.yml` is present in the export directory. If the file does
not exist, the command prints a message and exits without changes.

### CLI Usage

```
python post_grading.py finalize-late-penalties [--config PATH]
```

| Flag       | Default       | Description                |
|------------|---------------|----------------------------|
| `--config` | `config.yaml` | Path to configuration file |

### Examples

```bash
# Finalize late penalties with default config
python post_grading.py finalize-late-penalties

# Use a different config
python post_grading.py finalize-late-penalties --config config_hw2.yaml
```

### Standalone Alternative

The file `retroactive_late_penalties.py` in the project root is a standalone script
that performs a similar retroactive penalty application. It reads `config.yaml`
directly (no `--config` flag), loads submissions, and inserts late-penalty warning
boxes into `.tex` files that were graded before late penalties were enabled. Use
`finalize-late-penalties` for the standard workflow; the standalone script is for
one-off retroactive corrections.

---

## Recommended Workflow Order

A typical post-grading session runs these subcommands in sequence:

```bash
# 1. Adjust late penalties if PDF metadata is available
python post_grading.py finalize-late-penalties

# 2. Audit reports for LLM errors
python post_grading.py audit

# 3. If audit found issues, regrade the flagged students (command printed by audit),
#    then re-run the audit to confirm fixes

# 4. Split for TAs and have them enter scores in Gradescope
python post_grading.py split-for-tas --num-tas 3

# 5. Email feedback to students
python post_grading.py send-feedback --dry-run          # verify first
python post_grading.py send-feedback --test-recipients you@university.edu
python post_grading.py send-feedback                    # send for real
```
