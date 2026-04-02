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
Email notification module for LaTA grading system.

This module handles sending feedback PDFs to students via SMTP with:
- Secure credential prompting (no storage)
- Retry logic with exponential backoff
- Delivery tracking and error reporting
- Dry-run and test-recipient modes
"""

import smtplib
import ssl
import time
import getpass
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from email.message import EmailMessage

import yaml
from src.lata.ingest import StudentSubmission

# Import AppleScript mailer for macOS
try:
    from src.lata.applescript_mailer import (
        send_email_via_applescript,
        test_mail_configuration,
        get_mail_accounts
    )
    APPLESCRIPT_AVAILABLE = True
except ImportError:
    APPLESCRIPT_AVAILABLE = False


class FeedbackEmailer:
    """
    Handles sending feedback PDFs to students via SMTP.

    Features:
    - Secure credential prompting (getpass)
    - Retry logic with exponential backoff (2s, 4s, 8s)
    - Delivery tracking to YAML metadata
    - Dry-run mode for testing
    - Support for test recipient filtering
    """

    def __init__(self, config_path: str = "config.yaml", output_dir: str = None, is_corrections_mode: bool = False):
        """
        Initialize emailer with configuration.

        Args:
            config_path: Path to config.yaml
            output_dir: Output directory for feedback PDFs and delivery tracking
            is_corrections_mode: If True, use corrections email template
        """
        # Load configuration
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        self.config = config
        self.email_config = config.get('email', {})
        self.grading_config = config.get('grading', {})
        self.is_corrections_mode = is_corrections_mode

        # Email backend selection ('smtp' or 'applescript')
        self.email_backend = self.email_config.get('backend', 'smtp').lower()

        # Validate backend
        if self.email_backend not in ['smtp', 'applescript']:
            print(f"⚠️  Warning: Unknown email backend '{self.email_backend}', falling back to SMTP")
            self.email_backend = 'smtp'

        if self.email_backend == 'applescript' and not APPLESCRIPT_AVAILABLE:
            print("⚠️  Warning: AppleScript backend requested but not available, falling back to SMTP")
            self.email_backend = 'smtp'

        # SMTP settings
        self.smtp_server = self.email_config.get('smtp_server', 'mail.engr.oregonstate.edu')
        self.smtp_port = self.email_config.get('smtp_port', 25)
        self.use_ssl = self.email_config.get('use_ssl', False)
        self.use_tls = self.email_config.get('use_tls', True)
        self.timeout = self.email_config.get('timeout', 30)

        # Email content templates
        self.subject_template = self.email_config.get(
            'subject_template',
            '{assignment_name} has been graded, your feedback is ready'
        )
        self.from_name = self.email_config.get('from_name', 'LaTA Grading System')
        self.body_template = self.email_config.get('body_template', '')

        # Error handling settings
        self.max_retries = self.email_config.get('max_retries', 3)
        self.retry_delay = self.email_config.get('retry_delay', 2)
        self.batch_delay = self.email_config.get('batch_delay', 0.5)

        # Output directory and delivery tracking
        if output_dir is None:
            assignment_name = self.grading_config.get('assignment_name', 'Assignment')
            output_dir = f"data/output/{assignment_name}_Feedback"

        self.output_dir = Path(output_dir)
        self.delivery_file = self.output_dir / "email_delivery.yaml"

        # Initialize delivery tracking
        self.delivery_data = self._load_delivery_tracking()

        # AppleScript settings
        applescript_config = self.email_config.get('applescript', {})
        self.applescript_from_account = applescript_config.get('from_account', None)

        # SMTP connection (initialized during authenticate())
        self.smtp_connection = None
        self.from_email = None
        self.username = None
        self.password = None

    def _load_delivery_tracking(self) -> Dict:
        """Load existing delivery tracking data or create new"""
        if self.delivery_file.exists():
            with open(self.delivery_file, 'r') as f:
                return yaml.safe_load(f) or {}

        return {
            'metadata': {
                'last_run': None,
                'total_sent': 0,
                'total_failed': 0
            },
            'delivered': {},
            'failed': {}
        }

    def _save_delivery_tracking(self):
        """Save delivery tracking data to YAML file"""
        self.delivery_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.delivery_file, 'w') as f:
            yaml.dump(self.delivery_data, f, default_flow_style=False, sort_keys=False)

    def _validate_email(self, email: str) -> bool:
        """
        Validate email address format.

        Args:
            email: Email address to validate

        Returns:
            True if valid, False otherwise
        """
        # Basic email regex
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

        if not re.match(pattern, email):
            return False

        # Oregon State domain validation (safety check)
        if not email.endswith('@oregonstate.edu'):
            print(f"⚠️  Warning: Non-OSU email detected: {email}")

        return True

    def authenticate(self, username: str = None, password: str = None):
        """
        Authenticate with SMTP server and test connection.

        Args:
            username: SMTP username (prompts if None)
            password: SMTP password (prompts if None)
        """
        print("=" * 80)
        print("SMTP AUTHENTICATION")
        print("=" * 80)
        print()

        # Prompt for credentials if not provided
        if username is None:
            username = input(f"SMTP Username (your institutional email): ")

        if password is None:
            password = getpass.getpass(f"SMTP Password for {username}: ")

        self.username = username
        self.password = password
        self.from_email = username  # Use username as from_email

        print(f"📧 Connecting to {self.smtp_server}:{self.smtp_port}...")

        try:
            # Test SMTP connection
            if self.use_ssl:
                # Direct SSL connection (port 465)
                context = ssl.create_default_context()
                self.smtp_connection = smtplib.SMTP_SSL(
                    self.smtp_server,
                    self.smtp_port,
                    timeout=self.timeout,
                    context=context
                )
            else:
                # Standard connection with STARTTLS (port 25)
                self.smtp_connection = smtplib.SMTP(
                    self.smtp_server,
                    self.smtp_port,
                    timeout=self.timeout
                )

                if self.use_tls:
                    context = ssl.create_default_context()
                    self.smtp_connection.starttls(context=context)

            # Authenticate
            print(f"🔐 Authenticating as {username}...")
            self.smtp_connection.login(self.username, self.password)

            print("✅ Authentication successful!")
            print()

        except smtplib.SMTPAuthenticationError as e:
            print(f"❌ Authentication failed: {e}")
            print()
            print("Troubleshooting tips:")
            print("  - Verify your username and password are correct")
            print("  - OSU uses SSO with Duo 2FA - you may need an app-specific password")
            print("  - Contact IT support if authentication continues to fail")
            raise
        except Exception as e:
            print(f"❌ SMTP connection failed: {e}")
            print()
            print("Troubleshooting tips:")
            print(f"  - Verify SMTP server: {self.smtp_server}")
            print(f"  - Verify SMTP port: {self.smtp_port}")
            print("  - Check network connectivity")
            print("  - Try fallback port 465 with SSL if using port 25")
            raise

    def authenticate_applescript(self, from_account: str = None):
        """
        Set up AppleScript email sending (verify Mail.app is configured).

        Args:
            from_account: Email account to send from (prompts if None)
        """
        print("=" * 80)
        print("APPLESCRIPT EMAIL SETUP")
        print("=" * 80)
        print()

        # Test Mail.app configuration
        ready, message = test_mail_configuration()
        print(message)
        print()

        if not ready:
            raise RuntimeError("Mail.app is not properly configured. Please add an email account in Mail.app.")

        # Determine from account
        if from_account is None:
            from_account = self.applescript_from_account

        if from_account is None:
            # Prompt user to select account
            accounts = get_mail_accounts()
            if len(accounts) == 1:
                from_account = accounts[0]
                print(f"📧 Using account: {from_account}")
            else:
                print("Available accounts:")
                for i, account in enumerate(accounts):
                    print(f"  {i + 1}. {account}")
                print()
                choice = input(f"Select account to send from (1-{len(accounts)}): ")
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(accounts):
                        from_account = accounts[idx]
                    else:
                        raise ValueError("Invalid choice")
                except ValueError:
                    raise RuntimeError("Invalid account selection")

        # Store the from account
        self.from_email = from_account
        print(f"✅ AppleScript email ready to send from: {from_account}")
        print()

    def _create_email_message(self, student: StudentSubmission, pdf_path: Path) -> EmailMessage:
        """
        Construct MIME email message with PDF attachment.

        Args:
            student: StudentSubmission object
            pdf_path: Path to feedback PDF

        Returns:
            EmailMessage ready to send
        """
        msg = EmailMessage()

        # Email headers
        assignment_name = self.grading_config.get('assignment_name', 'Assignment')

        # Use different subject and body for corrections mode
        if self.is_corrections_mode:
            # Corrections mode: indicate final grade
            base_name = assignment_name.replace('_CORRECTIONS', '')
            subject = f"{base_name} corrections have been graded - final grade"

            # Corrections email body
            body = f"""Hello {student.student_name},

Your corrected submission for {base_name} has been graded. Please see the attached PDF for detailed feedback from the LaTA evaluation system.

This feedback represents your final grade on this assignment, including any points recovered through corrections and your original late penalty (if applicable).

If you have questions about your grade, please contact your instructor or TA.

Best regards,
LaTA Grading System"""
        else:
            # Normal mode: use configured templates
            subject = self.subject_template.format(assignment_name=assignment_name)
            body = self.body_template.format(
                student_name=student.student_name,
                assignment_name=assignment_name
            )

        msg['Subject'] = subject
        msg['From'] = f"{self.from_name} <{self.from_email}>"
        msg['To'] = student.student_email

        # Set email body
        msg.set_content(body)

        # Attach PDF
        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()

        msg.add_attachment(
            pdf_data,
            maintype='application',
            subtype='pdf',
            filename=pdf_path.name
        )

        return msg

    def _track_delivery(self, student_name: str, status: str, error_msg: str = None,
                       email: str = None, pdf_file: str = None, attempts: int = 1):
        """
        Record delivery status to metadata file.

        Args:
            student_name: Student name (safe filename format)
            status: 'delivered' or 'failed'
            error_msg: Error message if failed
            email: Student email address
            pdf_file: PDF filename
            attempts: Number of attempts made
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if status == 'delivered':
            self.delivery_data['delivered'][student_name] = {
                'email': email,
                'sent_at': timestamp,
                'pdf_file': pdf_file
            }
            self.delivery_data['metadata']['total_sent'] += 1

            # Remove from failed if previously there
            if student_name in self.delivery_data['failed']:
                del self.delivery_data['failed'][student_name]

        elif status == 'failed':
            self.delivery_data['failed'][student_name] = {
                'email': email,
                'attempts': attempts,
                'last_error': error_msg,
                'pdf_file': pdf_file
            }
            self.delivery_data['metadata']['total_failed'] += 1

        self.delivery_data['metadata']['last_run'] = timestamp
        self._save_delivery_tracking()

    def send_single_feedback(self, student: StudentSubmission, pdf_path: Path,
                            retry_count: int = 0, dry_run: bool = False) -> bool:
        """
        Send individual feedback email with retry logic.

        Args:
            student: StudentSubmission object
            pdf_path: Path to feedback PDF
            retry_count: Current retry attempt (0-indexed)
            dry_run: If True, simulate without sending

        Returns:
            True if successful, False if failed
        """
        # Validate email
        if not self._validate_email(student.student_email):
            print(f"❌ {student.student_name}: Invalid email address '{student.student_email}'")
            self._track_delivery(
                student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                status='failed',
                error_msg="Invalid email address format",
                email=student.student_email,
                pdf_file=pdf_path.name,
                attempts=1
            )
            return False

        # Check PDF exists
        if not pdf_path.exists():
            print(f"❌ {student.student_name}: PDF not found at {pdf_path}")
            self._track_delivery(
                student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                status='failed',
                error_msg="PDF file not found",
                email=student.student_email,
                pdf_file=pdf_path.name,
                attempts=1
            )
            return False

        # Check PDF size (warn if >5MB, reject if >25MB)
        pdf_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if pdf_size_mb > 25:
            print(f"❌ {student.student_name}: PDF too large ({pdf_size_mb:.1f}MB > 25MB limit)")
            self._track_delivery(
                student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                status='failed',
                error_msg=f"PDF exceeds size limit ({pdf_size_mb:.1f}MB)",
                email=student.student_email,
                pdf_file=pdf_path.name,
                attempts=1
            )
            return False
        elif pdf_size_mb > 5:
            print(f"⚠️  {student.student_name}: Large PDF ({pdf_size_mb:.1f}MB)")

        # Dry-run mode
        if dry_run:
            print(f"[DRY-RUN] Would send to {student.student_name} ({student.student_email}): {pdf_path.name}")
            return True

        # Route to appropriate backend
        if self.email_backend == 'applescript':
            return self._send_via_applescript(student, pdf_path, retry_count)
        else:
            return self._send_via_smtp(student, pdf_path, retry_count)

    def _send_via_smtp(self, student: StudentSubmission, pdf_path: Path, retry_count: int) -> bool:
        """Send email via SMTP (original implementation)"""
        try:
            # Create email message
            msg = self._create_email_message(student, pdf_path)

            # Send email
            self.smtp_connection.send_message(msg)

            print(f"✅ {student.student_name} ({student.student_email})")

            # Track successful delivery
            self._track_delivery(
                student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                status='delivered',
                email=student.student_email,
                pdf_file=pdf_path.name
            )

            return True

        except (smtplib.SMTPException, OSError) as e:
            # Network or server error - retry with exponential backoff
            if retry_count < self.max_retries:
                delay = self.retry_delay * (2 ** retry_count)
                print(f"⚠️  {student.student_name}: Send failed ({e}), retrying in {delay}s...")
                time.sleep(delay)
                return self._send_via_smtp(student, pdf_path, retry_count + 1)
            else:
                # Max retries exceeded
                print(f"❌ {student.student_name}: Failed after {self.max_retries + 1} attempts - {e}")
                self._track_delivery(
                    student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                    status='failed',
                    error_msg=str(e),
                    email=student.student_email,
                    pdf_file=pdf_path.name,
                    attempts=self.max_retries + 1
                )
                return False

        except Exception as e:
            # Unexpected error
            print(f"❌ {student.student_name}: Unexpected error - {e}")
            self._track_delivery(
                student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                status='failed',
                error_msg=f"Unexpected error: {str(e)}",
                email=student.student_email,
                pdf_file=pdf_path.name,
                attempts=retry_count + 1
            )
            return False

    def _send_via_applescript(self, student: StudentSubmission, pdf_path: Path, retry_count: int) -> bool:
        """Send email via AppleScript (Mac Mail.app)"""

        # Prepare email content
        assignment_name = self.grading_config.get('assignment_name', 'Assignment')

        # Use different subject and body for corrections mode
        if self.is_corrections_mode:
            # Corrections mode: indicate final grade
            base_name = assignment_name.replace('_CORRECTIONS', '')
            subject = f"{base_name} corrections have been graded - final grade"

            # Corrections email body
            body = f"""Hello {student.student_name},

Your corrected submission for {base_name} has been graded. Please see the attached PDF for detailed feedback from the LaTA evaluation system.

This feedback represents your final grade on this assignment, including any points recovered through corrections and your original late penalty (if applicable).

If you have questions about your grade, please contact your instructor or TA.

Best regards,
LaTA Grading System"""
        else:
            # Normal mode: use configured templates
            subject = self.subject_template.format(assignment_name=assignment_name)
            body = self.body_template.format(
                student_name=student.student_name,
                assignment_name=assignment_name
            )

        # Send via AppleScript
        success, error = send_email_via_applescript(
            to=student.student_email,
            subject=subject,
            body=body,
            attachment_path=str(pdf_path),
            from_account=self.from_email
        )

        if success:
            print(f"✅ {student.student_name} ({student.student_email})")

            # Track successful delivery
            self._track_delivery(
                student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                status='delivered',
                email=student.student_email,
                pdf_file=pdf_path.name
            )
            return True
        else:
            # Failed - retry with exponential backoff
            if retry_count < self.max_retries:
                delay = self.retry_delay * (2 ** retry_count)
                print(f"⚠️  {student.student_name}: Send failed ({error}), retrying in {delay}s...")
                time.sleep(delay)
                return self._send_via_applescript(student, pdf_path, retry_count + 1)
            else:
                # Max retries exceeded
                print(f"❌ {student.student_name}: Failed after {self.max_retries + 1} attempts - {error}")
                self._track_delivery(
                    student_name=student.student_name.replace(" ", "_").replace("(", "").replace(")", ""),
                    status='failed',
                    error_msg=error,
                    email=student.student_email,
                    pdf_file=pdf_path.name,
                    attempts=self.max_retries + 1
                )
                return False

    def _show_email_preview_and_confirm(self, pdf_matches: List[Tuple]) -> None:
        """
        Display a preview of all emails that will be sent and require user confirmation.
        This is a safety check to prevent sending emails to wrong students.

        Args:
            pdf_matches: List of (student, pdf_path) tuples
        """
        print()
        print("=" * 80)
        print("📋 EMAIL PREVIEW - PLEASE VERIFY BEFORE SENDING")
        print("=" * 80)
        print()
        print("The following emails will be sent:")
        print()

        # Show first 10 in detail, then summary for rest
        preview_limit = 10

        for i, (student, pdf_path) in enumerate(pdf_matches[:preview_limit]):
            pdf_size = pdf_path.stat().st_size / 1024  # KB
            print(f"{i+1:3d}. {student.student_name:<30} → {student.student_email}")
            print(f"     PDF: {pdf_path.name} ({pdf_size:.1f} KB)")
            print()

        if len(pdf_matches) > preview_limit:
            print(f"... and {len(pdf_matches) - preview_limit} more students")
            print()

        # Summary statistics
        total_size = sum(pdf_path.stat().st_size for _, pdf_path in pdf_matches) / (1024 * 1024)  # MB
        print("=" * 80)
        print(f"Total: {len(pdf_matches)} students | Total size: {total_size:.1f} MB")
        print("=" * 80)
        print()

        # Require explicit confirmation
        print("⚠️  IMPORTANT: Please verify the student names match the email addresses!")
        print()
        confirm = input("Type 'YES' to send these emails (anything else to cancel): ")

        if confirm != 'YES':
            print()
            print("❌ Cancelled by user. No emails were sent.")
            print()
            raise RuntimeError("Email sending cancelled by user")

        print()
        print("✅ Confirmed. Starting to send emails...")
        print()

    def send_feedback_batch(self, submissions: List[StudentSubmission],
                           dry_run: bool = False,
                           test_recipients: List[str] = None,
                           force_resend: bool = False):
        """
        Send feedback PDFs to all students in batch.

        Args:
            submissions: List of StudentSubmission objects
            dry_run: If True, simulate without sending
            test_recipients: List of email addresses to send to (testing mode)
            force_resend: If True, resend even if already delivered
        """
        print("=" * 80)
        print("SENDING FEEDBACK EMAILS")
        print("=" * 80)
        print()

        assignment_name = self.grading_config.get('assignment_name', 'Assignment')

        # Filter test recipients if specified
        if test_recipients:
            print(f"🧪 TEST MODE: Only sending to {len(test_recipients)} recipient(s)")
            submissions = [s for s in submissions if s.student_email in test_recipients]
            print(f"   Filtered to {len(submissions)} student(s)")
            print()

        if dry_run:
            print("🧪 DRY-RUN MODE: No emails will actually be sent")
            print()

        # Match PDFs to students
        pdf_matches = []
        missing_pdfs = []

        for student in submissions:
            # Generate safe filename (same logic as reporter.py)
            safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")

            # Check for versioned PDFs (_v2, _v3, etc.)
            pdf_path = self.output_dir / f"Feedback_{safe_name}.pdf"

            if not pdf_path.exists():
                # Try to find versioned PDF
                versioned_pdfs = list(self.output_dir.glob(f"Feedback_{safe_name}_v*.pdf"))
                if versioned_pdfs:
                    # Use latest version (highest number)
                    pdf_path = max(versioned_pdfs, key=lambda p: int(p.stem.split('_v')[-1]))
                else:
                    missing_pdfs.append(student.student_name)
                    continue

            # Check if already sent (skip unless force_resend)
            if not force_resend and safe_name in self.delivery_data['delivered']:
                print(f"⏭️  {student.student_name}: Already sent (use --force-resend to override)")
                continue

            pdf_matches.append((student, pdf_path))

        if missing_pdfs:
            print(f"⚠️  Warning: {len(missing_pdfs)} student(s) missing PDFs:")
            for name in missing_pdfs[:5]:  # Show first 5
                print(f"     - {name}")
            if len(missing_pdfs) > 5:
                print(f"     ... and {len(missing_pdfs) - 5} more")
            print()

        if not pdf_matches:
            print("❌ No emails to send (all already delivered or PDFs missing)")
            return

        # SAFETY CHECK: Show preview and require confirmation (unless dry-run or test mode)
        if not dry_run and not test_recipients:
            self._show_email_preview_and_confirm(pdf_matches)

        print(f"📊 Sending to {len(pdf_matches)} student(s)...")
        print()

        # Send emails
        sent_count = 0
        failed_count = 0

        for student, pdf_path in pdf_matches:
            success = self.send_single_feedback(student, pdf_path, dry_run=dry_run)

            if success:
                sent_count += 1
            else:
                failed_count += 1

            # Delay between emails to avoid rate limiting
            if not dry_run and success:
                time.sleep(self.batch_delay)

        print()
        print("=" * 80)
        print(f"Sent: {sent_count} | Failed: {failed_count}")
        print("=" * 80)

    def get_delivery_report(self) -> Dict:
        """
        Generate summary of sent/failed emails.

        Returns:
            Dictionary with delivery statistics
        """
        delivered = self.delivery_data.get('delivered', {})
        failed = self.delivery_data.get('failed', {})

        failures = []
        for student_name, info in failed.items():
            failures.append({
                'student': student_name.replace("_", " "),
                'email': info.get('email', 'Unknown'),
                'error': info.get('last_error', 'Unknown error'),
                'attempts': info.get('attempts', 0)
            })

        return {
            'total': len(delivered) + len(failed),
            'sent': len(delivered),
            'failed': len(failed),
            'failures': failures
        }

    def __del__(self):
        """Close SMTP connection on cleanup"""
        if self.smtp_connection:
            try:
                self.smtp_connection.quit()
            except:
                pass
