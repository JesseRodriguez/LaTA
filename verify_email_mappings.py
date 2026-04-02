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
Email Mapping Verification Script

This script thoroughly verifies that PDFs are correctly mapped to student emails
before you send any actual emails. Run this BEFORE sending feedback to students!

Usage:
    python verify_email_mappings.py
    python verify_email_mappings.py --config custom_config.yaml
    python verify_email_mappings.py --output-csv verification_report.csv
"""

import argparse
import csv
import yaml
from pathlib import Path
from src.lata.ingest import GradescopeIngestor
from src.lata.emailer import FeedbackEmailer


def verify_mappings(config_path: str = "config.yaml", output_csv: str = None):
    """
    Verify that all PDFs are correctly mapped to student emails.

    Args:
        config_path: Path to config file
        output_csv: Optional path to save verification report as CSV
    """
    print("=" * 80)
    print("EMAIL MAPPING VERIFICATION")
    print("=" * 80)
    print()

    # Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    grading_config = config.get('grading', {})
    paths_config = config.get('paths', {})

    assignment_name = grading_config.get('assignment_name', 'Assignment')
    export_dir = Path(paths_config.get('export_dir', 'data/input'))
    output_dir = Path(f"data/output/{assignment_name}_Feedback")

    print(f"📁 Assignment: {assignment_name}")
    print(f"📂 Export directory: {export_dir}")
    print(f"📂 Output directory: {output_dir}")
    print()

    # 1. Load student submissions
    print("1️⃣  Loading student submissions...")
    anonymize = grading_config.get('anonymize', False)
    ingestor = GradescopeIngestor(
        export_dir=str(export_dir),
        anonymize=anonymize
    )
    ingest_result = ingestor.load()

    print(f"   ✅ Loaded {len(ingest_result.submissions)} student submissions")
    print()

    # 2. Initialize emailer (to use same PDF matching logic)
    emailer = FeedbackEmailer(config_path=config_path, output_dir=str(output_dir))

    # 3. Match PDFs to students (same logic as send_feedback_batch)
    print("2️⃣  Matching PDFs to student emails...")
    pdf_matches = []
    missing_pdfs = []

    for student in ingest_result.submissions:
        safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")
        pdf_path = output_dir / f"Feedback_{safe_name}.pdf"

        if not pdf_path.exists():
            # Try versioned PDFs
            versioned_pdfs = list(output_dir.glob(f"Feedback_{safe_name}_v*.pdf"))
            if versioned_pdfs:
                pdf_path = max(versioned_pdfs, key=lambda p: int(p.stem.split('_v')[-1]))
            else:
                missing_pdfs.append(student)
                continue

        pdf_matches.append((student, pdf_path))

    print(f"   ✅ Found {len(pdf_matches)} PDF matches")
    if missing_pdfs:
        print(f"   ⚠️  {len(missing_pdfs)} students missing PDFs")
    print()

    # 4. Display detailed verification table
    print("3️⃣  Verification Table")
    print("=" * 80)
    print()

    # Column headers
    print(f"{'#':<5} {'Student Name':<30} {'Email':<35} {'PDF File':<30}")
    print("-" * 80)

    for i, (student, pdf_path) in enumerate(pdf_matches, 1):
        pdf_size = pdf_path.stat().st_size / 1024  # KB
        print(f"{i:<5} {student.student_name:<30} {student.student_email:<35} {pdf_path.name:<30}")

    print()
    print("=" * 80)

    # 5. Run safety checks
    print()
    print("4️⃣  Running Safety Checks...")
    print()

    issues_found = []

    # Check 1: Email domain validation
    print("   🔍 Checking email domains...")
    non_osu_emails = []
    for student, _ in pdf_matches:
        if not student.student_email.endswith('@oregonstate.edu'):
            non_osu_emails.append((student.student_name, student.student_email))

    if non_osu_emails:
        print(f"   ⚠️  Found {len(non_osu_emails)} non-OSU email(s):")
        for name, email in non_osu_emails[:5]:
            print(f"      - {name}: {email}")
        if len(non_osu_emails) > 5:
            print(f"      ... and {len(non_osu_emails) - 5} more")
        issues_found.append("Non-OSU emails detected")
    else:
        print("   ✅ All emails are @oregonstate.edu")

    # Check 2: Duplicate emails
    print("   🔍 Checking for duplicate emails...")
    email_counts = {}
    for student, _ in pdf_matches:
        email_counts[student.student_email] = email_counts.get(student.student_email, 0) + 1

    duplicates = {email: count for email, count in email_counts.items() if count > 1}
    if duplicates:
        print(f"   ⚠️  Found {len(duplicates)} duplicate email(s):")
        for email, count in duplicates.items():
            print(f"      - {email} appears {count} times")
        issues_found.append("Duplicate emails detected")
    else:
        print("   ✅ No duplicate emails")

    # Check 3: PDF file size validation
    print("   🔍 Checking PDF sizes...")
    large_pdfs = []
    for student, pdf_path in pdf_matches:
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if size_mb > 5:
            large_pdfs.append((student.student_name, pdf_path.name, size_mb))

    if large_pdfs:
        print(f"   ⚠️  Found {len(large_pdfs)} large PDF(s) (>5MB):")
        for name, pdf_name, size in large_pdfs[:5]:
            print(f"      - {name}: {pdf_name} ({size:.1f} MB)")
        if len(large_pdfs) > 5:
            print(f"      ... and {len(large_pdfs) - 5} more")
        # This is just a warning, not an issue
    else:
        print("   ✅ All PDFs are reasonable size (<5MB)")

    # Check 4: Name-to-PDF consistency
    print("   🔍 Checking name-to-PDF mapping consistency...")
    naming_issues = []
    for student, pdf_path in pdf_matches:
        safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")
        expected_pdf = f"Feedback_{safe_name}.pdf"

        # Allow versioned PDFs
        if not (pdf_path.name == expected_pdf or pdf_path.name.startswith(f"Feedback_{safe_name}_v")):
            naming_issues.append((student.student_name, expected_pdf, pdf_path.name))

    if naming_issues:
        print(f"   ⚠️  Found {len(naming_issues)} name-to-PDF mismatch(es):")
        for name, expected, actual in naming_issues[:5]:
            print(f"      - {name}: expected '{expected}', got '{actual}'")
        issues_found.append("Name-to-PDF mapping issues")
    else:
        print("   ✅ All PDF names match student names correctly")

    print()

    # 6. Summary
    print("=" * 80)
    print("📊 VERIFICATION SUMMARY")
    print("=" * 80)
    print()
    print(f"Total students:        {len(ingest_result.submissions)}")
    print(f"PDFs found:            {len(pdf_matches)}")
    print(f"Missing PDFs:          {len(missing_pdfs)}")
    print(f"Ready to send:         {len(pdf_matches)}")
    print()

    if issues_found:
        print("⚠️  ISSUES FOUND:")
        for issue in issues_found:
            print(f"   - {issue}")
        print()
        print("❌ Please review and fix the issues above before sending emails!")
    else:
        print("✅ All safety checks passed!")
        print()
        print("   The PDF-to-email mappings look correct.")
        print("   You can proceed with sending emails.")

    print()

    # 7. Show missing PDFs if any
    if missing_pdfs:
        print("⚠️  MISSING PDFs:")
        print()
        for student in missing_pdfs[:10]:
            print(f"   - {student.student_name} ({student.student_email})")
        if len(missing_pdfs) > 10:
            print(f"   ... and {len(missing_pdfs) - 10} more")
        print()

    # 8. Export to CSV if requested
    if output_csv:
        print(f"💾 Saving verification report to: {output_csv}")
        with open(output_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Student Name', 'Email', 'PDF File', 'PDF Size (KB)', 'Status'])

            for student, pdf_path in pdf_matches:
                size_kb = pdf_path.stat().st_size / 1024
                writer.writerow([
                    student.student_name,
                    student.student_email,
                    pdf_path.name,
                    f"{size_kb:.1f}",
                    "Ready"
                ])

            for student in missing_pdfs:
                writer.writerow([
                    student.student_name,
                    student.student_email,
                    "MISSING",
                    "0",
                    "Missing PDF"
                ])

        print(f"   ✅ Report saved to: {output_csv}")
        print()

    print("=" * 80)

    # Return status
    return len(issues_found) == 0 and len(missing_pdfs) == 0


def main():
    parser = argparse.ArgumentParser(description="Verify email-to-PDF mappings before sending")
    parser.add_argument('--config', default='config.yaml', help="Path to config file")
    parser.add_argument('--output-csv', help="Save verification report to CSV file")

    args = parser.parse_args()

    try:
        all_clear = verify_mappings(config_path=args.config, output_csv=args.output_csv)

        if all_clear:
            print()
            print("🎉 All clear! You can safely send emails.")
            return 0
        else:
            print()
            print("⚠️  Please review and fix issues before sending.")
            return 1

    except Exception as e:
        print()
        print(f"❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
