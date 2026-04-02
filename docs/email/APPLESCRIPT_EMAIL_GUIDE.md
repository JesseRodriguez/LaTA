# AppleScript Email Integration Guide

## Overview

LaTA now supports sending feedback emails through Mac Mail.app using AppleScript automation. This eliminates the need for SMTP authentication and leverages your existing Mail.app configuration.

## Prerequisites

1. **macOS** - AppleScript email only works on macOS
2. **Mail.app configured** - You must have at least one email account set up in Mail.app
3. **Institutional email** - Your Oregon State (or other institutional) email should be configured in Mail.app

## Quick Start

### 1. Test Mail.app Configuration

First, verify that Mail.app is properly configured:

```bash
python src/lata/applescript_mailer.py
```

This will check if Mail.app is running and list configured accounts.

### 2. Send a Test Email

Send a test email to yourself to verify everything works:

```bash
python test_applescript_mail.py --to your_email@oregonstate.edu --dry-run
```

If the dry-run succeeds, send a real test email:

```bash
python test_applescript_mail.py --to your_email@oregonstate.edu
```

Optionally attach a PDF:

```bash
python test_applescript_mail.py --to your_email@oregonstate.edu --pdf "data/output/Feedback_Test.pdf"
```

### 3. Configure LaTA to Use AppleScript

The configuration is already set in `config.yaml`:

```yaml
email:
  backend: "applescript"  # ← Already configured!

  applescript:
    from_account: null  # Will prompt you to select account
```

If you want to skip the account selection prompt, specify your email:

```yaml
email:
  backend: "applescript"

  applescript:
    from_account: "your_email@oregonstate.edu"
```

### 4. Send Feedback Emails

Use the normal post-grading workflow:

```bash
# Dry-run mode (test without sending)
python post_grading.py send-feedback --dry-run

# Send to a test recipient first
python post_grading.py send-feedback --test-recipients your_email@oregonstate.edu

# Send to all students
python post_grading.py send-feedback
```

## How It Works

### Authentication Flow

**AppleScript Backend (No Password Needed!)**:
1. LaTA checks if Mail.app is running
2. Lists configured email accounts
3. Prompts you to select an account (or uses `from_account` from config)
4. Starts sending emails through Mail.app

**SMTP Backend (Traditional)**:
1. Prompts for username and password
2. Connects to SMTP server (smtp.office365.com)
3. Authenticates with credentials
4. Sends emails directly via SMTP

### Email Sending

When using AppleScript backend:
- Emails are created in Mail.app
- PDFs are attached automatically
- Emails appear in your Mail.app Sent folder
- Uses your existing Mail.app authentication (no password entry!)

## Switching Between Backends

You can easily switch between AppleScript and SMTP by changing one line in `config.yaml`:

```yaml
email:
  backend: "applescript"  # Use Mac Mail.app
  # backend: "smtp"       # Use SMTP server
```

All other settings (templates, retry logic, delivery tracking) work identically.

## Advantages of AppleScript

✅ **No Password Entry** - Uses Mail.app's existing authentication
✅ **No SMTP Configuration** - Works immediately if Mail.app is set up
✅ **Sent Folder Integration** - Emails appear in your Sent folder
✅ **OAuth 2.0 Support** - Works with modern auth (Microsoft 365, Gmail, etc.)
✅ **Zero Configuration** - Just select your account and go

## Troubleshooting

### Mail.app Not Running

**Error**: "Mail.app is not running"

**Solution**: The script will attempt to start Mail.app automatically. If it fails, manually open Mail.app before running the script.

### No Email Accounts Configured

**Error**: "No email accounts configured in Mail.app"

**Solution**:
1. Open Mail.app
2. Go to Mail > Settings > Accounts
3. Add your institutional email account
4. Run the test script again

### Automation Permission Denied

**Error**: "execution error: Not authorized to send Apple events"

**Solution**:
1. Open System Settings > Privacy & Security > Automation
2. Find Terminal (or Python/your IDE)
3. Enable permission for "Mail"
4. Run the script again

### Account Not Found

**Error**: "Account 'your_email@oregonstate.edu' not found in Mail.app"

**Solution**:
- Verify the account is configured in Mail.app (check Accounts preferences)
- Make sure the email address in config.yaml matches exactly
- Or set `from_account: null` to be prompted to select the account

### Email Stuck in Outbox

**Issue**: Emails appear in Outbox but don't send

**Solution**:
- Check that Mail.app is online (Window > Connection Doctor)
- Verify your internet connection
- Check if the account is set to send mail online (may be in Offline mode)
- Try manually sending an email from Mail.app to verify account works

## Features Preserved

The AppleScript backend maintains all existing features:

- ✅ Retry logic with exponential backoff (2s, 4s, 8s delays)
- ✅ Delivery tracking to YAML (`email_delivery.yaml`)
- ✅ Dry-run mode (`--dry-run`)
- ✅ Test recipient filtering (`--test-recipients`)
- ✅ Force resend (`--force-resend`)
- ✅ PDF validation (size limits, existence checks)
- ✅ Email validation
- ✅ Batch delay (0.5s between emails)

## Technical Details

### AppleScript Template

The system uses this AppleScript template to send emails:

```applescript
tell application "Mail"
    set newMessage to make new outgoing message with properties {
        subject: "Assignment has been graded",
        content: "Email body...",
        visible: false
    }

    tell newMessage
        make new to recipient at end of to recipients with properties {
            address: "student@oregonstate.edu"
        }

        set sender to "your_email@oregonstate.edu"

        make new attachment with properties {
            file name: POSIX file "/path/to/Feedback.pdf"
        } at after last paragraph

        send
    end tell
end tell
```

### File Locations

- **AppleScript Module**: `src/lata/applescript_mailer.py`
- **Test Script**: `test_applescript_mail.py`
- **Updated Emailer**: `src/lata/emailer.py`
- **Post-Grading Script**: `post_grading.py`
- **Configuration**: `config.yaml`

### Delivery Tracking

Delivery tracking works identically for both backends. Metadata is saved to:

```
data/output/ME373_HW1_Feedback/email_delivery.yaml
```

Format:
```yaml
metadata:
  last_run: "2026-01-18 15:30:00"
  total_sent: 47
  total_failed: 3

delivered:
  John_Smith:
    email: "jsmith@oregonstate.edu"
    sent_at: "2026-01-18 15:15:03"
    pdf_file: "Feedback_John_Smith.pdf"

failed:
  Bob_Johnson:
    email: "bjohnson@invalid.com"
    attempts: 4
    last_error: "AppleScript error: Invalid email address"
    pdf_file: "Feedback_Bob_Johnson.pdf"
```

## Limitations

- **macOS Only**: AppleScript integration only works on macOS
- **Mail.app Required**: Mail.app must be configured with an active email account
- **Manual Setup**: User must initially configure their email account in Mail.app
- **Automation Permissions**: User must grant automation permissions on first run

For non-macOS systems, use the SMTP backend instead (set `backend: "smtp"`).

## Support

If you encounter issues:

1. Run the test script with a simple test email first
2. Check the troubleshooting section above
3. Verify Mail.app works by manually sending an email
4. Check system permissions (Privacy & Security > Automation)
5. Fall back to SMTP if AppleScript continues to fail

## Example Workflow

```bash
# 1. Grade assignments (generates PDFs)
python grade.py

# 2. Test AppleScript email (dry-run)
python test_applescript_mail.py --to your_email@oregonstate.edu --dry-run

# 3. Send real test email to yourself
python test_applescript_mail.py --to your_email@oregonstate.edu

# 4. Verify test email arrived, then send to students
python post_grading.py send-feedback --test-recipients your_email@oregonstate.edu another_test@oregonstate.edu

# 5. If tests pass, send to all students
python post_grading.py send-feedback

# 6. Check delivery report
cat data/output/ME373_HW1_Feedback/email_delivery.yaml
```

## Migration from SMTP

If you were previously using SMTP:

1. Update `config.yaml`: `backend: "applescript"`
2. Configure your email in Mail.app (if not already)
3. Run test script to verify: `python test_applescript_mail.py --to your_email@oregonstate.edu`
4. All existing delivery tracking, retry logic, and features continue to work

No other changes needed!
