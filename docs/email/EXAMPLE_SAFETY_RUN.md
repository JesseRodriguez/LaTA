# Example: Safe Email Sending Session

Here's a walkthrough of how to safely send feedback emails to students.

## Session Walkthrough

### Step 1: Verify Email Mappings

```bash
$ uv run verify_email_mappings.py
================================================================================
EMAIL MAPPING VERIFICATION
================================================================================

Assignment: Physics101_HW1
Export directory: data/input/Physics101_HW1
Output directory: data/output/Physics101_HW1_Feedback

1. Loading student submissions...
   Loaded 50 student submissions

2. Matching PDFs to student emails...
   Found 50 PDF matches

3. Verification Table
================================================================================

#     Student Name                   Email                              PDF File
--------------------------------------------------------------------------------
1     Alice Johnson                  johnsona@university.edu            Feedback_Alice_Johnson.pdf
2     Bob Martinez                   martineb@university.edu            Feedback_Bob_Martinez.pdf
3     Carol Williams                 williac@university.edu             Feedback_Carol_Williams.pdf
...
50    Zara Thompson                  thompsoz@university.edu            Feedback_Zara_Thompson.pdf

================================================================================

4. Running Safety Checks...

   Checking email domains...
   All emails are @university.edu
   Checking for duplicate emails...
   No duplicate emails
   Checking PDF sizes...
   All PDFs are reasonable size (<5MB)
   Checking name-to-PDF mapping consistency...
   All PDF names match student names correctly

================================================================================
VERIFICATION SUMMARY
================================================================================

Total students:        50
PDFs found:            50
Missing PDFs:          0
Ready to send:         50

All safety checks passed!

   The PDF-to-email mappings look correct.
   You can proceed with sending emails.

================================================================================
```

**All checks passed! Safe to proceed.**

### Step 2: Test Email System

```bash
$ uv run test_email_safety.py --to you@university.edu
================================================================================
EMAIL SAFETY TEST
================================================================================

1. Testing Mail.app configuration...
   Testing Mail.app configuration...

   Mail.app is running
   Found 1 configured account(s):
      - you@university.edu

Mail.app is ready to send emails

2. Available email accounts:
   1. you@university.edu

3. Selecting email account...
   Using: you@university.edu

4. Creating test PDF...
   Created: /var/folders/.../LaTA_Email_Test.pdf
   Size: 1.2 KB

5. Sending test email...

   From: you@university.edu
   To:   you@university.edu
   PDF:  LaTA_Email_Test.pdf

   Send this test email? (y/n): y

   Sending email via AppleScript...

================================================================================
SUCCESS! Test email sent
================================================================================

Next steps:
  1. Check your inbox for the test email
  2. Verify the PDF is attached correctly
  3. Check Mail.app Sent folder to confirm

If everything looks good, run the verification script:
  python verify_email_mappings.py

Then test with a few students:
  python post_grading.py send-feedback --test-recipients email1 email2
```

**Test email received successfully!**

### Step 3: Test with Real Student PDFs (To Yourself)

```bash
$ uv run post_grading.py send-feedback --test-recipients you@university.edu
================================================================================
APPLESCRIPT EMAIL SETUP
================================================================================

Testing Mail.app configuration...

   Mail.app is running
   Found 1 configured account(s):
      - you@university.edu

Mail.app is ready to send emails

Using account: you@university.edu
AppleScript email ready to send from: you@university.edu

================================================================================
SENDING FEEDBACK EMAILS
================================================================================

TEST MODE: Only sending to 1 recipient(s)
   Filtered to 1 student(s)

Sending to 1 student(s)...

Sent: Your Name (you@university.edu)

================================================================================
Sent: 1 | Failed: 0
================================================================================
```

Check your inbox - you should receive an email with one of the real student PDFs.

**PDF received and looks correct!**

### Step 4: Send to All Students (With Confirmation)

```bash
$ uv run post_grading.py send-feedback
================================================================================
APPLESCRIPT EMAIL SETUP
================================================================================

Testing Mail.app configuration...

   Mail.app is running
   Found 1 configured account(s):
      - you@university.edu

Mail.app is ready to send emails

Using account: you@university.edu
AppleScript email ready to send from: you@university.edu

================================================================================
SENDING FEEDBACK EMAILS
================================================================================


================================================================================
EMAIL PREVIEW - PLEASE VERIFY BEFORE SENDING
================================================================================

The following emails will be sent:

  1. Alice Johnson                  -> johnsona@university.edu
     PDF: Feedback_Alice_Johnson.pdf (234.5 KB)

  2. Bob Martinez                   -> martineb@university.edu
     PDF: Feedback_Bob_Martinez.pdf (198.2 KB)

  3. Carol Williams                 -> williac@university.edu
     PDF: Feedback_Carol_Williams.pdf (256.1 KB)

  4. David Chen                     -> chend@university.edu
     PDF: Feedback_David_Chen.pdf (187.9 KB)

  5. Emily Rodriguez                -> rodrige@university.edu
     PDF: Feedback_Emily_Rodriguez.pdf (243.3 KB)

... and 45 more students

================================================================================
Total: 50 students | Total size: 11.8 MB
================================================================================

IMPORTANT: Please verify the student names match the email addresses!

Type 'YES' to send these emails (anything else to cancel): YES

Confirmed. Starting to send emails...

Sending to 50 student(s)...

Sent: Alice Johnson (johnsona@university.edu)
Sent: Bob Martinez (martineb@university.edu)
Sent: Carol Williams (williac@university.edu)
Sent: David Chen (chend@university.edu)
Sent: Emily Rodriguez (rodrige@university.edu)
... (45 more)
Sent: Zara Thompson (thompsoz@university.edu)

================================================================================
Sent: 50 | Failed: 0
================================================================================
```

**All emails sent successfully!**

### Step 5: Verify Delivery

Check the delivery tracking file:

```bash
$ cat data/output/Physics101_HW1_Feedback/email_delivery.yaml
```

```yaml
metadata:
  last_run: '2026-01-18 16:45:33'
  total_sent: 50
  total_failed: 0

delivered:
  Alice_Johnson:
    email: johnsona@university.edu
    sent_at: '2026-01-18 16:44:15'
    pdf_file: Feedback_Alice_Johnson.pdf
  Bob_Martinez:
    email: martineb@university.edu
    sent_at: '2026-01-18 16:44:18'
    pdf_file: Feedback_Bob_Martinez.pdf
  # ... (48 more students)

failed: {}
```

**All 50 students received their feedback!**

## What If Something Goes Wrong?

### Example: Cancelled Before Sending

```bash
Type 'YES' to send these emails (anything else to cancel): no

Cancelled by user. No emails were sent.
```

No emails were sent - safe!

### Example: Found Issues During Verification

```bash
$ uv run verify_email_mappings.py

4. Running Safety Checks...

   Checking for duplicate emails...
   Found 2 duplicate email(s):
      - jsmith@university.edu appears 2 times

ISSUES FOUND:
   - Duplicate emails detected

Please review and fix the issues above before sending emails!
```

**Action**: Review the Gradescope export to understand why there are duplicates, fix the issue, and run verification again.

## Summary

This example shows:
1. Verification catches issues before sending
2. Test mode lets you verify with yourself first
3. Confirmation prompt gives you one last chance to review
4. Progress is tracked and logged
5. You can verify delivery after sending

**Total time**: ~5 minutes to safely send to 50 students.
