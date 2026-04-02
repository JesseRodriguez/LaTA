# Quick Start Guide - 30 Second Setup

## For Each New Assignment

### Step 1: Edit One Line (10 seconds)

Open `config.yaml` and change the number:

```yaml
expected_problems: 3  # <-- Change this
```

For HW2 → `expected_problems: 2`
For HW3 → `expected_problems: 3`
For HW5 → `expected_problems: 5`

### Step 2: Create Zip (10 seconds)

```bash
cd gradescope_autograder
zip -r autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml
```

### Step 3: Upload to Gradescope (10 seconds)

1. Go to assignment → Configure Autograder
2. Upload `autograder.zip`
3. Done!

---

## What Students Will See

### Valid Submission (3 problems found, 3 expected) ✅
```
✓ Submission validated successfully
Score: 1.0 / 1.0
```

### Missing Problem (2 problems found, 3 expected) ❌
```
✗ Wrong number of problems
Expected 3 problems, but found 2.
Your submission has: 1, 2
This assignment requires exactly 3 problems.
Check that you haven't missed any problems.

Score: 0.0 / 1.0
```

Students can resubmit immediately after fixing!

---

## Real Example

**Scenario:** ME 373 HW3 has 3 problems. Some students only submitted 2.

**Before:**
- Students pass autograder ✅
- LaTA fails during grading ❌
- Instructor has to email students ⏰
- Students resubmit days later ⏰

**After (with this autograder):**
- Students submit → autograder runs in 30 seconds
- "Expected 3 problems, found 2" ❌
- Students fix and resubmit immediately ✅
- LaTA gets clean submissions ✅

**Time saved:** Hours of back-and-forth emails per assignment

---

## No Config = No Count Check

If you don't want to check problem count:

```yaml
# Comment out or set to null
# expected_problems: 3
```

Validator will still check:
- Single .tex file ✅
- Document structure ✅
- Problem markers exist ✅
- No duplicates ✅

---

## Need Help?

See `CONFIGURATION.md` for advanced options.
