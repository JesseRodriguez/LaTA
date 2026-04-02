# Email Sending - Quick Start

## TL;DR - Safe Email Sending

```bash
# 1. Verify everything is correct
python verify_email_mappings.py

# 2. Test email system
python test_email_safety.py --to your_email@oregonstate.edu

# 3. Test with yourself using real PDFs
python post_grading.py send-feedback --test-recipients your_email@oregonstate.edu

# 4. Send to everyone (you'll get a confirmation prompt)
python post_grading.py send-feedback
```

## Built-In Safety Features

✅ **Automatic confirmation prompt** - Must type `YES` to send
✅ **Preview of all emails** - See exactly who gets what
✅ **Test mode** - Send to specific people only
✅ **Dry-run mode** - Simulate without sending
✅ **Already-sent tracking** - Won't spam students

## The Confirmation Prompt

When you run `send-feedback`, you'll see:

```
📋 EMAIL PREVIEW - PLEASE VERIFY BEFORE SENDING

  1. John Smith → jsmith@oregonstate.edu
     PDF: Feedback_John_Smith.pdf (245.3 KB)

  2. Jane Doe → jdoe@oregonstate.edu
     PDF: Feedback_Jane_Doe.pdf (198.7 KB)

... and 48 more students

Total: 50 students | Total size: 12.3 MB

⚠️  IMPORTANT: Please verify the student names match the email addresses!

Type 'YES' to send these emails (anything else to cancel): _
```

**Only type YES if the mappings look correct!**

## Common Commands

```bash
# Verify mappings (ALWAYS run this first!)
python verify_email_mappings.py

# Export verification to CSV for review
python verify_email_mappings.py --output-csv report.csv

# Test AppleScript email works
python test_email_safety.py --to your_email@oregonstate.edu

# Test with specific students
python post_grading.py send-feedback --test-recipients email1 email2 email3

# Dry-run (see what would happen)
python post_grading.py send-feedback --dry-run

# Send to everyone (with confirmation)
python post_grading.py send-feedback

# Resend to everyone (skip already-sent check)
python post_grading.py send-feedback --force-resend
```

## Safety Workflow (30 seconds)

1. `python verify_email_mappings.py` ← Check this passes ✅
2. `python post_grading.py send-feedback --test-recipients you@oregonstate.edu` ← Test with yourself
3. Check your email - PDF correct? ✅
4. `python post_grading.py send-feedback` ← Send to everyone
5. Review the preview, type `YES` if correct

That's it! 🎉

## Need More Details?

Read the full guide: `EMAIL_SAFETY_GUIDE.md`
