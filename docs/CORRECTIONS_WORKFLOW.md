# Corrections Workflow Guide

This document describes the corrections workflow in LaTA: a second grading pass
that allows students to recover points on problems they originally got wrong.
Corrections are graded against the same rubric but with specific rules around
extra credit preservation, late penalty carryover, and zero-scored item
restrictions.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Config Setup for Corrections](#config-setup-for-corrections)
4. [How Extra Credit Is Preserved](#how-extra-credit-is-preserved)
5. [Running Corrections Grading](#running-corrections-grading)
6. [The --regrade Flag](#the---regrade-flag)
7. [Post-Grading: Audit in Corrections Mode](#post-grading-audit-in-corrections-mode)
8. [Post-Grading: TA Splits in Corrections Mode](#post-grading-ta-splits-in-corrections-mode)
9. [Zero-Scored Items](#zero-scored-items)
10. [End-to-End Walkthrough](#end-to-end-walkthrough)
11. [Troubleshooting](#troubleshooting)

---

## Overview

After the original assignment has been graded and returned to students, the
instructor may open a "corrections" assignment on Gradescope. Students re-submit
their LaTeX file with fixes to the problems they got wrong. LaTA then grades the
corrections submission against the same rubric, but with these key differences:

- **Extra credit items are removed from the rubric** before grading. Students
  cannot re-earn extra credit in corrections; original extra credit is carried
  forward automatically from the first grading pass.
- **Late penalties are carried forward** from the original submission rather than
  recalculated from the corrections submission timestamp.
- **Zero-scored items** (rubric sections where the student scored 0 on the
  original) are tracked and flagged in the corrections report. Per course policy,
  these items cannot recover points.
- **Students who did not submit the original assignment** receive a placeholder
  report explaining that corrections are only valid with an original submission.

---

## Prerequisites

Before running corrections grading, you must have:

1. **A completed original grading run.** The original assignment (e.g.,
   `Assignment1`) must have been fully graded, producing output in
   `data/output/Assignment1_Feedback/`.

2. **The original grading metadata file.** This file lives at
   `data/output/Assignment1_Feedback/grading_metadata.yaml` and is created
   automatically during the original grading run. It stores per-student data
   including:
   - `extra_credit_earned` -- a mapping of problem group to extra credit points
   - `late_penalty` -- the original late penalty percentage
   - `zero_scored_items` -- list of rubric items that scored 0

3. **A Gradescope export of the corrections submissions.** Download the export
   and place it in your `data/input/` directory (e.g.,
   `data/input/Assignment1_CORRECTIONS_Export`).

---

## Config Setup for Corrections

Copy your original `config.yaml` and modify the following fields. The critical
change is the `assignment_name` -- it **must** end with `_CORRECTIONS`.

```yaml
grading:
  # The suffix _CORRECTIONS triggers corrections mode throughout the pipeline.
  # The base assignment name is derived by stripping this suffix.
  assignment_name: "Assignment1_CORRECTIONS"

paths:
  # Point to the corrections export directory
  export_dir: "data/input/Assignment1_CORRECTIONS_Export"

  # The rubric file stays the same as the original assignment.
  # LaTA automatically filters out extra credit items from the rubric
  # at runtime when corrections mode is detected.
  rubric_file: "assignment1_rubric.yaml"

  # The solution file also stays the same.
  solution_file: "data/solutions/assignment1_solution.tex"
```

Everything else in the config (model settings, late penalty settings, email
templates, etc.) can remain unchanged.

### What Happens Automatically

When `assignment_name` ends with `_CORRECTIONS`, LaTA performs these steps at
startup in `grade.py`:

1. Derives the base assignment name by stripping `_CORRECTIONS` (e.g.,
   `Assignment1_CORRECTIONS` becomes `Assignment1`).
2. Verifies that `data/output/Assignment1_Feedback/grading_metadata.yaml`
   exists. If it does not, grading aborts with an error.
3. Filters all items marked `is_extra_credit: true` out of the rubric. The LLM
   never sees extra credit criteria during corrections grading.
4. Passes `is_corrections_mode=True` and `base_assignment="Assignment1"` to the
   reporter, which loads the original grading metadata.

---

## How Extra Credit Is Preserved

Extra credit can only be earned during the original grading pass. During
corrections, LaTA restores original extra credit scores automatically.

### During Original Grading

When `generate_report()` runs in normal mode, it calls
`_extract_extra_credit_metadata()` to identify all extra credit items where the
student earned points. This data is saved into `grading_metadata.yaml` under
each student's entry:

```yaml
John_Smith:
  submission_created_at: "2026-01-16 22:30:00.000000000 Z"
  version: 1
  late_penalty: 0.0
  zero_scored_items: []
  extra_credit_earned:
    "Problem 1 (b)": 2.0
    "Problem 3": 1.0
```

### During Corrections Grading

When `generate_report()` runs in corrections mode, it:

1. Looks up the student in `original_metadata` (loaded from the base
   assignment's `grading_metadata.yaml`).
2. Reads the `extra_credit_earned` dictionary.
3. Creates **synthetic GradeItem objects** for each extra credit award with:
   - `criterion_name` set to `"Original Extra Credit (preserved)"`
   - `score` set to the original points earned
   - `max_score` set to `0.0` (extra credit does not count toward maximum)
   - `is_extra_credit` set to `True`
4. Appends these synthetic items to `grade_result.items`.
5. Recalculates the `total_score` to include the restored extra credit.

In the generated PDF, extra credit sections show the preserved points with the
label "Original Extra Credit (preserved)" rather than re-grading the extra
credit criteria.

---

## Running Corrections Grading

Once your `config.yaml` is set up for corrections, run grading exactly as you
would for the original assignment:

```bash
uv run python grade.py
```

LaTA will print a banner confirming corrections mode:

```
CORRECTIONS MODE detected
   Base assignment: Assignment1
   Original metadata: data/output/Assignment1_Feedback/

   Filtering extra credit items from rubric (corrections mode)
   Removed 3 extra credit items from grading rubric
```

Output goes to `data/output/Assignment1_CORRECTIONS_Feedback/`.

### Grading a Single Student

```bash
uv run python grade.py --student "John Smith"
```

### What Happens for Students Without an Original Submission

If a student submitted corrections but has no entry in the original
`grading_metadata.yaml`, LaTA generates a placeholder PDF instead of grading.
The placeholder report states:

> You submitted a corrections assignment, but there is no record of an original
> submission for the base assignment.

The student receives a score of 0/0 and is directed to contact their instructor.

---

## The --regrade Flag

Use `--regrade` to force re-grading specific students without creating versioned
reports. This is useful after fixing rubric issues or when the audit identifies
problems.

```bash
# Regrade a single student
uv run python grade.py --regrade "John Smith"

# Regrade multiple students
uv run python grade.py --regrade "John Smith" "Jane Doe" "Bob Jones"

# Match by anonymized ID
uv run python grade.py --regrade "ANON_042"
```

The `--regrade` flag:

- Skips the "already graded" check (forces fresh grading).
- Overwrites the existing report without creating a `_v2` suffix.
- Accepts partial name matches (case-insensitive) and anonymized IDs.
- Can be combined with corrections mode (the config determines the mode, not the
  flag).

---

## Post-Grading: Audit in Corrections Mode

Run the audit the same way as for original grading:

```bash
uv run python post_grading.py audit
```

The audit detects corrections mode from `assignment_name` in your config and
adjusts its behavior:

### Corrections-Specific Audit Behavior

1. **"Original Extra Credit (preserved)" is whitelisted.** The audit normally
   flags any criterion name that does not appear in the rubric as a fabricated
   criterion. In corrections mode, the synthetic criterion `"Original Extra
   Credit (preserved)"` is recognized as legitimate and skipped.

2. **Pure extra credit sections may be absent.** In normal mode, the audit flags
   any rubric section missing from the summary table. In corrections mode,
   sections that contain only extra credit items (where `max=0` and `ec_max>0`)
   are allowed to be absent from the report since extra credit is not re-graded.

3. **Missing feedback sections are still flagged** for non-EC sections. If a
   regular rubric section is missing from a corrections report, it is flagged as
   an issue.

The audit still generates regrade commands at the end, using the same format:

```bash
uv run python grade.py --regrade "Student Name 1" "Student Name 2"
```

---

## Post-Grading: TA Splits in Corrections Mode

Split feedback PDFs for TA entry into Gradescope:

```bash
uv run python post_grading.py split-for-tas --num-tas 3
```

### Corrections-Specific TA Split Behavior

In normal mode, TA splits include **placeholder pages** for students who
submitted a PDF to Gradescope but did not submit a TeX file. The placeholder
instructs the TA to enter 0 points and move on.

In corrections mode, **placeholders are not generated** for missing students.
Only students who actually submitted a `.tex` corrections file (and therefore
have a graded feedback PDF) are included in the TA split PDFs. The rationale is
that corrections are optional, so missing students simply did not submit
corrections and do not need a placeholder.

The log output will confirm this behavior:

```
CORRECTIONS: Only including students with .tex submissions (no placeholders)
```

---

## Zero-Scored Items

Zero-scored items are rubric sections (rows in the grading summary table) where
the student earned 0 out of the available points on the original submission. Per
course policy, these items cannot recover points in corrections.

### How They Are Tracked

During original grading, `_identify_zero_scored_items()` in the reporter groups
grade items by `problem_group` and checks if the total score for that group is
0. Items are recorded as compact identifiers:

- `"Problem 1 (a)"` becomes `"1a"`
- `"Problem 3"` becomes `"3"`

These identifiers are stored in `grading_metadata.yaml` under each student's
`zero_scored_items` list.

### How They Appear in Corrections Reports

During corrections grading, the reporter reads `zero_scored_items` from the
original metadata and passes them to the LaTeX template. The corrections feedback
PDF displays a prominent red box listing the zero-scored items with a message
explaining that these items cannot recover points.

Students still receive feedback on their corrections to these items, but the
score remains 0.

---

## End-to-End Walkthrough

This section walks through the complete corrections workflow from start to
finish.

### Step 1: Complete Original Grading

```bash
# Ensure config.yaml points to the original assignment
# assignment_name: "Assignment1"

uv run python grade.py
```

Verify that `data/output/Assignment1_Feedback/grading_metadata.yaml` was
created and contains entries for all graded students.

### Step 2: Run Post-Grading on Original

```bash
uv run python post_grading.py audit
uv run python post_grading.py split-for-tas --num-tas 3
```

Fix any issues found by the audit using `--regrade` before proceeding to
corrections.

### Step 3: Open Corrections on Gradescope

Create a new assignment on Gradescope for corrections submissions. Download the
export once students have submitted.

### Step 4: Update config.yaml for Corrections

```yaml
grading:
  assignment_name: "Assignment1_CORRECTIONS"

paths:
  export_dir: "data/input/Assignment1_CORRECTIONS_Export"
  # rubric_file and solution_file remain the same
```

### Step 5: Run Corrections Grading

```bash
uv run python grade.py
```

Review the output for any issues. The console will show extra credit restoration
and late penalty carryover for each student.

### Step 6: Audit and Split Corrections

```bash
uv run python post_grading.py audit
uv run python post_grading.py split-for-tas --num-tas 3
```

### Step 7: Send Feedback (Optional)

```bash
uv run python post_grading.py send-feedback --dry-run
uv run python post_grading.py send-feedback
```

---

## Troubleshooting

### "Original metadata not found" Error

```
ERROR: Original metadata not found at data/output/Assignment1_Feedback/grading_metadata.yaml
Cannot proceed with corrections mode.
```

**Cause:** The original assignment was never graded, or the output directory was
moved/deleted.

**Fix:** Ensure the original grading has completed. The path is derived from the
base assignment name (i.e., `assignment_name` with `_CORRECTIONS` stripped). The
metadata file must exist at `data/output/<base_name>_Feedback/grading_metadata.yaml`.

### Extra Credit Not Appearing in Corrections Reports

**Cause:** The `extra_credit_earned` field is missing from the original
`grading_metadata.yaml`. This happens if the original grading was run with an
older version of LaTA that did not save extra credit metadata.

**Fix:** Re-run the original grading to regenerate `grading_metadata.yaml` with
the `extra_credit_earned` field. Then re-run corrections grading:

```bash
# Temporarily switch config back to original assignment
# assignment_name: "Assignment1"
uv run python grade.py

# Switch config back to corrections
# assignment_name: "Assignment1_CORRECTIONS"
uv run python grade.py
```

### Missing Sections in Audit Output

**Cause:** In corrections mode, the audit may flag missing sections if the LLM
did not produce output for certain rubric sections. Pure extra credit sections
are automatically excluded from this check, but regular sections are still
expected.

**Fix:** Use `--regrade` on the flagged students:

```bash
uv run python grade.py --regrade "Student Name"
```

### Student Has No Original Submission but Submitted Corrections

LaTA generates a placeholder report for these students automatically. The
placeholder states that corrections are only valid with an original submission
and assigns a score of 0/0. No manual intervention is needed unless the student
contacts you about a missing original submission that should exist.

### Late Penalty Appears Wrong in Corrections

Corrections reports use the **original** late penalty percentage stored in
`grading_metadata.yaml`, not the corrections submission timestamp. If the
original late penalty was incorrect, fix it in the original grading first, then
re-run corrections.

### Rubric Filtering Removes Too Many Items

LaTA filters out **all** items with `is_extra_credit: true` from the rubric
during corrections mode. If a rubric item is incorrectly marked as extra credit,
it will be silently excluded from corrections grading. Verify your rubric file
to ensure `is_extra_credit` flags are correct before running corrections.
