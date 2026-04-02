# Configuration Guide for LaTeX Submission Validator

## Quick Start for Each Assignment

For each new assignment, follow these steps:

### 1. Edit `config.yaml`

Open `gradescope_autograder/config.yaml` and set the expected number of problems:

```yaml
# For HW 3 with 3 problems:
expected_problems: 3

# For HW 4 with 5 problems:
expected_problems: 5
```

### 2. Re-create the Zip File

```bash
cd gradescope_autograder
zip -r autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml
```

**Important:** Include `config.yaml` in the zip!

### 3. Upload to Gradescope

- Go to your assignment's "Configure Autograder" page
- Upload the new `autograder.zip`
- Wait for Docker build (2-5 minutes)

### 4. Test

Upload a test submission to verify the configuration works.

---

## Configuration Options

### Option 1: Check Problem Count (Recommended)

**Best for:** Catching students who forgot a problem or added extras

```yaml
expected_problems: 3
```

**What it checks:**
- ✅ Submission has exactly 3 `\Prob{}` markers
- ✗ Fails if 2 problems (missing one)
- ✗ Fails if 4 problems (extra one)

**Example error message:**
```
✗ Wrong number of problems
Expected 3 problems, but found 2.
Your submission has: 1, 2
This assignment requires exactly 3 problems.
```

---

### Option 2: Check Specific Problem Numbers (Stricter)

**Best for:** Non-sequential numbering (e.g., "Problem 2, 4, 6")

```yaml
expected_problem_numbers: [2, 4, 6]
```

**What it checks:**
- ✅ Submission has `\Prob{2}`, `\Prob{4}`, `\Prob{6}` (in any order)
- ✗ Fails if wrong numbers (e.g., 1, 2, 3)
- ✗ Fails if missing any number

**Example error message:**
```
✗ Incorrect problem numbers
Expected problems: 2, 4, 6
Found problems: 1, 2, 3
```

---

### Option 3: No Check (Accept Any Number)

**Best for:** Assignments with variable problem counts

```yaml
# expected_problems: null
# OR comment out the line:
# expected_problems: 3
```

**What it checks:**
- ✅ At least 1 problem marker exists
- ✅ No duplicates
- ⚠️ Does NOT enforce specific count

---

## Example Configurations

### Standard HW Assignment (3 problems)
```yaml
expected_problems: 3
```

### Exam with 5 problems
```yaml
expected_problems: 5
```

### Assignment with specific numbering
```yaml
# If assignment says "Do problems 1, 3, and 5"
expected_problem_numbers: [1, 3, 5]
```

### Optional problems (variable count)
```yaml
# No check - students can submit 2-5 problems
# expected_problems: null
```

---

## Workflow for Multiple Assignments

### Method 1: Separate Zip Per Assignment (Recommended)

```bash
# For HW2
cd gradescope_autograder
sed -i '' 's/expected_problems:.*/expected_problems: 2/' config.yaml
zip -r hw2_autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml

# For HW3
sed -i '' 's/expected_problems:.*/expected_problems: 3/' config.yaml
zip -r hw3_autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml

# Upload hw2_autograder.zip to HW2 on Gradescope
# Upload hw3_autograder.zip to HW3 on Gradescope
```

### Method 2: Re-use Same Directory

```bash
# Before each assignment, update config and re-zip
cd gradescope_autograder
nano config.yaml  # Edit expected_problems
zip -r autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml
# Upload to Gradescope
```

---

## Testing Locally Before Upload

```bash
# Create test submission with wrong number
mkdir -p test/
echo '\documentclass{article}\begin{document}\Prob{1}\Prob{2}\end{document}' > test/hw.tex

# Run validator
python3 validator.py test/ results.json

# Check output
cat results.json | python3 -m json.tool | grep '"score"'
# Should show "score": 0.0 if 2 problems but expected 3
```

---

## Troubleshooting

### "Expected 3 problems, but found 2"
**Student Issue:** Missing a problem or forgot `\Prob{N}` marker
**Fix:** Student needs to add the missing problem section

### "Expected problems: 1, 2, 3 Found: 1, 2, 4"
**Student Issue:** Wrong problem number (typo)
**Fix:** Student should check problem numbers match assignment

### Config not working after upload
**Instructor Issue:** Forgot to include `config.yaml` in zip
**Fix:** Re-create zip with: `zip -r autograder.zip ... config.yaml`

---

## Advanced: Assignment-Specific Messages

You can customize error messages by editing `validator.py`, but for most cases, the default messages with `expected_problems` are sufficient.

---

## Summary

**For 99% of assignments, just do this:**

1. Edit `expected_problems: N` in `config.yaml`
2. Run: `zip -r autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml`
3. Upload to Gradescope

**Time required:** ~30 seconds per assignment
