# Email Safety Guide

## Overview

This guide ensures you safely send feedback emails to students without accidentally sending PDFs to the wrong people. Follow these steps carefully before sending any emails!

## Safety Features Built Into LaTA

### 1. Pre-Send Confirmation (Automatic)

When you run `python post_grading.py send-feedback`, the system will:

1. **Show you a preview** of all emails that will be sent
2. **Display the mapping** of student names → emails → PDF files
3. **Require explicit confirmation** - you must type `YES` to proceed
4. **Allow cancellation** - any other input cancels the operation

Example output:
```
================================================================================
📋 EMAIL PREVIEW - PLEASE VERIFY BEFORE SENDING
================================================================================

The following emails will be sent:

  1. John Smith                     → jsmith@oregonstate.edu
     PDF: Feedback_John_Smith.pdf (245.3 KB)

  2. Jane Doe                       → jdoe@oregonstate.edu
     PDF: Feedback_Jane_Doe.pdf (198.7 KB)

... and 48 more students

================================================================================
Total: 50 students | Total size: 12.3 MB
================================================================================

⚠️  IMPORTANT: Please verify the student names match the email addresses!

Type 'YES' to send these emails (anything else to cancel):
```

### 2. Test Mode (--test-recipients)

Send emails to only specific test addresses first:

```bash
# Send ONLY to yourself
python post_grading.py send-feedback --test-recipients your_email@oregonstate.edu

# Send to a few test addresses
python post_grading.py send-feedback --test-recipients email1@oregonstate.edu email2@oregonstate.edu
```

**The confirmation prompt is SKIPPED in test mode** (faster testing).

### 3. Dry-Run Mode (--dry-run)

Simulate sending without actually sending anything:

```bash
python post_grading.py send-feedback --dry-run
```

Shows what would be sent, but doesn't actually send any emails.

### 4. Already-Sent Tracking

The system tracks which emails have been sent and automatically skips them:

```
⏭️  John Smith: Already sent (use --force-resend to override)
```

To resend to everyone (e.g., after fixing PDFs):
```bash
python post_grading.py send-feedback --force-resend
```

## Recommended Safety Workflow

### Step 1: Verify Mappings BEFORE Sending

Run the verification script first:

```bash
python verify_email_mappings.py
```

This will:
- ✅ Show all student → email → PDF mappings
- ✅ Check for duplicate emails
- ✅ Validate email domains (@oregonstate.edu)
- ✅ Check PDF sizes
- ✅ Verify name-to-PDF consistency
- ✅ List any missing PDFs

**DO NOT PROCEED** if this script reports issues!

Optional: Export verification report to CSV:
```bash
python verify_email_mappings.py --output-csv verification_report.csv
```

Then review the CSV in Excel/Numbers to visually verify the mappings.

### Step 2: Test Email System

Test that AppleScript email works:

```bash
python test_email_safety.py --to your_email@oregonstate.edu
```

This sends a test email to yourself with a test PDF attachment.

### Step 3: Test with Real Student PDFs

Send to yourself using a real student's PDF:

```bash
# First, find a student email in your export
# Then send ONLY to yourself, not to the student

python post_grading.py send-feedback --test-recipients your_email@oregonstate.edu
```

Verify:
- ✅ Email arrives in your inbox
- ✅ PDF is attached
- ✅ PDF opens correctly
- ✅ PDF content looks correct
- ✅ Student name in email matches the PDF
- ✅ Email appears in Mail.app Sent folder

### Step 4: Send to Small Test Group

Pick 2-3 students you know well (TAs, friends in the class, etc.):

```bash
python post_grading.py send-feedback --test-recipients \
    ta@oregonstate.edu \
    friend@oregonstate.edu \
    another_test@oregonstate.edu
```

Ask them to verify:
- They received the correct PDF (their name matches)
- The PDF opens and looks correct
- The email content is appropriate

### Step 5: Send to Everyone

Once all tests pass:

```bash
python post_grading.py send-feedback
```

**You will see the confirmation prompt** showing all students who will receive emails.

Review the preview carefully:
- ✅ Student names make sense
- ✅ Email addresses look correct
- ✅ PDF filenames match student names
- ✅ No duplicate emails
- ✅ Total count matches expected number

Type `YES` to confirm and send.

## Safety Checks Performed Automatically

The verification script checks for:

### 1. Email Domain Validation
- Warns if any emails are not @oregonstate.edu
- Helps catch typos or incorrect email addresses

### 2. Duplicate Email Detection
- Finds if the same email appears multiple times
- Prevents sending multiple PDFs to one student

### 3. PDF Size Validation
- Warns about PDFs larger than 5MB
- Flags PDFs larger than 25MB (will be rejected)

### 4. Name-to-PDF Consistency
- Verifies PDF filename matches student name
- Catches mismatches like `Feedback_John_Smith.pdf` going to Jane Doe

### 5. Missing PDFs
- Lists students who have no PDF generated
- Prevents sending emails without attachments

## Common Issues and Solutions

### Issue: "Student names don't match email addresses"

**Cause**: Gradescope export might have nicknames, middle names, or different name formats

**Solution**:
1. Review the verification report CSV
2. Manually check a few random mappings
3. If suspicious, use test mode to send to yourself first
4. Verify the PDF content matches the email recipient

### Issue: "Duplicate email addresses detected"

**Cause**: Same student submitted multiple times, or data entry error

**Solution**:
1. Check the verification report to see which emails are duplicated
2. Review the Gradescope export to understand why
3. Use `--force-resend` if you need to resend to same student
4. Or manually remove duplicates from the export before grading

### Issue: "Missing PDFs for some students"

**Cause**: Grading failed for those students, or they were skipped

**Solution**:
1. Check `data/output/failed_submissions.yaml` for grading errors
2. Manually grade those students or fix errors
3. Re-run `python grade.py` for those students
4. Then send emails

### Issue: "Accidentally sent to wrong student"

**Prevention**: Follow the safety workflow above!

**If it happens**:
1. Send a quick apology email to the student
2. Ask them to delete the email (explain it was meant for another student)
3. Send the correct PDF to the correct student
4. Send the correct PDF to the original student who got the wrong one
5. Use delivery tracking YAML to verify who got what

## Delivery Tracking

All sent emails are tracked in:
```
data/output/ME373_HW1_Feedback/email_delivery.yaml
```

This file records:
- ✅ Who received emails (with timestamp)
- ❌ Who failed to receive emails (with error message)
- 📊 Total sent/failed counts

You can review this file after sending to verify everyone got their email.

## Emergency: Cancel Mid-Send

If you start sending and realize there's an issue:

1. **Press Ctrl+C** in the terminal to stop the script
2. Check `email_delivery.yaml` to see who already received emails
3. Fix the issue
4. Re-run with `--force-resend` if needed (or the system will skip already-sent)

## Best Practices

✅ **DO**:
- Run verification script before every send
- Test with yourself first
- Use test mode with small groups
- Review the confirmation prompt carefully
- Keep delivery tracking files for records

❌ **DON'T**:
- Skip the verification step
- Send to all students without testing first
- Ignore warnings about duplicate emails or missing PDFs
- Modify PDFs after verification but before sending
- Delete delivery tracking files (keep for audit trail)

## Summary: Complete Safety Checklist

Before sending feedback emails to students:

- [ ] Run `python verify_email_mappings.py` - all checks pass?
- [ ] Run `python test_email_safety.py --to your_email@oregonstate.edu` - test email received?
- [ ] Review verification report CSV (if exported)
- [ ] Test with `--test-recipients your_email@oregonstate.edu` - correct PDF received?
- [ ] Test with 2-3 real students using `--test-recipients` - feedback confirmed?
- [ ] Review confirmation prompt when sending to everyone
- [ ] Type `YES` only after verifying the preview looks correct
- [ ] Monitor the sending progress for errors
- [ ] Review `email_delivery.yaml` after sending - everyone got their email?

If you followed all these steps, you can be confident that:
- ✅ Each student received the correct PDF
- ✅ No PDFs went to wrong students
- ✅ All emails were delivered successfully
- ✅ You have a record of who received what

## Questions or Issues?

If you're unsure about any step:
1. STOP and don't send yet
2. Review this guide again
3. Run the verification script
4. Test with yourself first
5. When in doubt, use `--test-recipients` mode

**It's better to be cautious than to send wrong PDFs to students!**
