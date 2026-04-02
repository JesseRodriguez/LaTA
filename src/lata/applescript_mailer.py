#!/usr/bin/env python3
# LaTA Grading System - Automated grading workflow using open-source LLMs.
# Copyright (C) 2026 Jesse A. Rodriguez
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
AppleScript-based email sending module for macOS.

This module provides email sending functionality using AppleScript to control
the native Mac Mail.app. This avoids SMTP authentication issues by leveraging
the user's existing Mail.app configuration.

Requirements:
- macOS with Mail.app configured
- Python subprocess module
- User must grant automation permissions on first run
"""

import subprocess
import re
from pathlib import Path
from typing import Optional, List, Tuple


def verify_mail_app_running() -> bool:
    """
    Check if Mail.app is currently running.

    Returns:
        True if Mail.app is running, False otherwise
    """
    script = '''
    tell application "System Events"
        return (name of processes) contains "Mail"
    end tell
    '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip() == 'true'
    except Exception:
        return False


def start_mail_app() -> bool:
    """
    Start Mail.app if it's not running.

    Returns:
        True if Mail.app was started successfully, False otherwise
    """
    script = '''
    tell application "Mail"
        activate
    end tell
    '''

    try:
        subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        # Wait a moment for Mail.app to fully start
        import time
        time.sleep(2)
        return verify_mail_app_running()
    except Exception as e:
        print(f"Failed to start Mail.app: {e}")
        return False


def get_mail_accounts() -> List[str]:
    """
    Get list of email accounts configured in Mail.app.

    Returns:
        List of email addresses configured in Mail.app
    """
    script = '''
    tell application "Mail"
        set accountList to {}
        repeat with acc in accounts
            set end of accountList to email addresses of acc
        end repeat
        return accountList
    end tell
    '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )

        # Parse the output - AppleScript returns comma-separated values
        output = result.stdout.strip()
        if not output or output == '':
            return []

        # Handle nested lists from multiple accounts
        # Output looks like: "email1@example.com, email2@example.com"
        accounts = [email.strip() for email in output.split(',')]
        return [acc for acc in accounts if acc and '@' in acc]

    except Exception as e:
        print(f"Failed to get Mail.app accounts: {e}")
        return []


def validate_from_account(email: str) -> bool:
    """
    Check if the specified email account exists in Mail.app.

    Args:
        email: Email address to validate

    Returns:
        True if account exists, False otherwise
    """
    accounts = get_mail_accounts()
    return email in accounts


def send_email_via_applescript(
    to: str,
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    from_account: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Send an email using AppleScript to control Mail.app.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Email body content (plain text)
        attachment_path: Optional path to file to attach
        from_account: Optional email address to send from (must exist in Mail.app)

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """

    # Verify Mail.app is running
    if not verify_mail_app_running():
        print("   Mail.app is not running. Starting...")
        if not start_mail_app():
            return False, "Failed to start Mail.app"

    # Validate from account if specified
    if from_account and not validate_from_account(from_account):
        return False, f"Account '{from_account}' not found in Mail.app"

    # Escape special characters for AppleScript string
    def escape_applescript_string(s: str) -> str:
        """Escape quotes and backslashes for AppleScript"""
        if s is None:
            return ""
        # Escape backslashes first, then quotes
        s = s.replace('\\', '\\\\')
        s = s.replace('"', '\\"')
        return s

    # Build AppleScript command
    to_escaped = escape_applescript_string(to)
    subject_escaped = escape_applescript_string(subject)
    body_escaped = escape_applescript_string(body)

    # Base script - create email
    script = f'''
tell application "Mail"
    set newMessage to make new outgoing message with properties {{subject:"{subject_escaped}", content:"{body_escaped}", visible:false}}

    tell newMessage
        make new to recipient at end of to recipients with properties {{address:"{to_escaped}"}}
'''

    # Add from account if specified
    if from_account:
        from_escaped = escape_applescript_string(from_account)
        script += f'''
        set sender to "{from_escaped}"
'''

    # Add attachment if specified
    if attachment_path:
        # Convert to absolute path and verify it exists
        pdf_path = Path(attachment_path).resolve()
        if not pdf_path.exists():
            return False, f"Attachment file not found: {pdf_path}"

        pdf_path_str = str(pdf_path)
        # AppleScript needs POSIX path
        script += f'''
        make new attachment with properties {{file name:POSIX file "{pdf_path_str}"}} at after last paragraph
'''

    # Send the email
    script += '''
        send
    end tell
end tell
'''

    try:
        # Execute AppleScript
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )

        return True, None

    except subprocess.CalledProcessError as e:
        # Parse AppleScript error message
        error_msg = e.stderr.strip() if e.stderr else str(e)

        # Extract useful error information
        if 'execution error' in error_msg.lower():
            # Try to extract the actual error message
            match = re.search(r'execution error: (.+?)(?:\s*\(-\d+\))?$', error_msg, re.IGNORECASE)
            if match:
                error_msg = match.group(1).strip()

        return False, f"AppleScript error: {error_msg}"

    except subprocess.TimeoutExpired:
        return False, "Email sending timed out (Mail.app may be unresponsive)"

    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def test_mail_configuration() -> Tuple[bool, str]:
    """
    Test Mail.app configuration and report status.

    Returns:
        Tuple of (is_ready: bool, status_message: str)
    """
    print("🔍 Testing Mail.app configuration...")
    print()

    # Check if Mail.app is running
    if not verify_mail_app_running():
        print("   ⚠️  Mail.app is not running")
        print("   Attempting to start Mail.app...")
        if start_mail_app():
            print("   ✅ Mail.app started successfully")
        else:
            return False, "❌ Failed to start Mail.app. Please open Mail.app manually and configure an email account."

    print("   ✅ Mail.app is running")

    # Get configured accounts
    accounts = get_mail_accounts()

    if not accounts:
        return False, "❌ No email accounts configured in Mail.app. Please add an account in Mail.app preferences."

    print(f"   ✅ Found {len(accounts)} configured account(s):")
    for account in accounts:
        print(f"      - {account}")

    print()
    return True, "✅ Mail.app is ready to send emails"


if __name__ == "__main__":
    # Self-test when run directly
    print("=" * 80)
    print("AppleScript Mailer Module - Self Test")
    print("=" * 80)
    print()

    ready, message = test_mail_configuration()
    print(message)
    print()

    if ready:
        print("✅ AppleScript email sending is ready to use!")
    else:
        print("❌ AppleScript email sending requires configuration")
        print()
        print("Next steps:")
        print("  1. Open Mail.app")
        print("  2. Add your institutional email account")
        print("  3. Run this test again")
