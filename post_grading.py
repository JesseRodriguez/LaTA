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
Post-grading workflow utilities for LaTA.

This script provides various post-grading tasks:
1. Split assignments among TAs for Gradescope entry
2. Send feedback PDFs to students via email
3. Audit feedback reports for quality issues

Usage:
    python post_grading.py split-for-tas --num-tas 3
    python post_grading.py send-feedback --dry-run
    python post_grading.py audit
"""

import argparse
import yaml
from pathlib import Path
from datetime import datetime
from typing import List
from src.lata.ingest import GradescopeIngestor
from src.lata.emailer import FeedbackEmailer
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import io
import subprocess
import re


def finalize_late_penalties(config_path: str = "config.yaml"):
    """
    Synchronizes late penalties between TeX and PDF submissions.
    Uses the LATER of the two submission timestamps to determine the final late penalty.
    Regenerates PDFs if penalties need to be corrected.

    Args:
        config_path: Path to config.yaml
    """
    print("=" * 80)
    print("FINALIZE LATE PENALTIES")
    print("=" * 80)
    print()

    # Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    assignment_name = config['grading']['assignment_name']
    output_dir = Path(f"data/output/{assignment_name}_Feedback")
    export_dir = Path(config['paths']['export_dir'])

    if not output_dir.exists():
        print(f"❌ Output directory not found: {output_dir}")
        return

    # Check if PDF submission metadata exists
    pdf_metadata_path = export_dir / 'pdf_submission_metadata.yml'

    if not pdf_metadata_path.exists():
        print("📝 No PDF submission metadata found - skipping late penalty synchronization")
        print("   (This feature requires pdf_submission_metadata.yml)")
        return

    print("📄 Loading submission metadata...")

    # Load TeX submission metadata
    ingestor = GradescopeIngestor(
        config['paths']['export_dir'],
        anonymize=config['grading']['anonymize']
    )
    ingest_result = ingestor.load()

    # Create mapping: safe_name -> TeX submission timestamp
    tex_timestamps = {}
    for student in ingest_result.submissions:
        safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")
        tex_timestamps[safe_name] = {
            'name': student.student_name,
            'timestamp': student.created_at,  # This is already a string from Gradescope
            'sid': student.student_sid
        }

    # Load PDF submission metadata
    with open(pdf_metadata_path, 'r') as f:
        pdf_metadata = yaml.safe_load(f)

    # Create mapping: safe_name -> PDF submission timestamp
    pdf_timestamps = {}
    for submission_id, sub_data in pdf_metadata.items():
        if not sub_data or ':submitters' not in sub_data:
            continue

        submitter = sub_data[':submitters'][0]
        student_name = submitter[':name']
        student_sid = submitter[':sid']
        created_at = sub_data[':created_at']
        safe_name = student_name.replace(" ", "_").replace("(", "").replace(")", "")

        pdf_timestamps[safe_name] = {
            'name': student_name,
            'timestamp': created_at,
            'sid': student_sid
        }

    print(f"✅ Found {len(tex_timestamps)} TeX submissions")
    print(f"✅ Found {len(pdf_timestamps)} PDF submissions")
    print()

    # Initialize reporter for late penalty calculation
    from src.lata.reporter import LatexReporter
    reporter = LatexReporter(output_dir=str(output_dir))

    # Find students who need penalty adjustment
    adjustments_needed = []

    for safe_name in tex_timestamps.keys():
        # Check if student submitted both TeX and PDF
        if safe_name not in pdf_timestamps:
            continue

        tex_time_str = tex_timestamps[safe_name]['timestamp']
        pdf_time_str = pdf_timestamps[safe_name]['timestamp']

        # Parse timestamps - handle both string and datetime objects
        # Strip timezone info to ensure we can compare them
        try:
            # Handle TeX timestamp
            if isinstance(tex_time_str, datetime):
                # Remove timezone info if present (convert to naive datetime)
                tex_time = tex_time_str.replace(tzinfo=None)
            elif isinstance(tex_time_str, str):
                tex_time = datetime.strptime(tex_time_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
            else:
                raise ValueError(f"Unexpected type for TeX timestamp: {type(tex_time_str)}")

            # Handle PDF timestamp
            if isinstance(pdf_time_str, datetime):
                # Remove timezone info if present (convert to naive datetime)
                pdf_time = pdf_time_str.replace(tzinfo=None)
            elif isinstance(pdf_time_str, str):
                pdf_time = datetime.strptime(pdf_time_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
            else:
                raise ValueError(f"Unexpected type for PDF timestamp: {type(pdf_time_str)}")
        except Exception as e:
            print(f"⚠️  Warning: Could not parse timestamps for {safe_name}: {e}")
            continue

        # Determine which submission was later
        later_timestamp = max(tex_time, pdf_time)
        used_timestamp = tex_time  # The one currently used in the feedback PDF

        if later_timestamp != used_timestamp:
            # Need to recalculate with PDF timestamp
            adjustments_needed.append({
                'safe_name': safe_name,
                'student_name': tex_timestamps[safe_name]['name'],
                'tex_time': tex_time,
                'pdf_time': pdf_time,
                'later_time': later_timestamp,
                'later_time_str': later_timestamp.strftime("%Y-%m-%d %H:%M:%S") + ".000000000 Z"
            })

    if not adjustments_needed:
        print("✅ All late penalties are already correct (PDF submitted before or same time as TeX)")
        print()
        return

    print(f"🔄 Found {len(adjustments_needed)} students needing late penalty adjustment")
    print("   (PDF was submitted later than TeX)")
    print()

    # Process each adjustment
    fixed_count = 0
    for adj in adjustments_needed:
        safe_name = adj['safe_name']
        student_name = adj['student_name']

        # Find the feedback PDF and TeX file
        feedback_pdf_path = output_dir / f"Feedback_{safe_name}.pdf"
        tex_log_path = output_dir / "logs" / f"Feedback_{safe_name}.tex"

        if not tex_log_path.exists():
            print(f"⚠️  {student_name}: No TeX log found, skipping")
            continue

        if not feedback_pdf_path.exists():
            print(f"⚠️  {student_name}: No feedback PDF found, skipping")
            continue

        # Read the TeX file to extract current scores
        with open(tex_log_path, 'r') as f:
            tex_content = f.read()

        # Extract scores from TeX (look for total score before penalty)
        # Pattern: \textbf{Points Before Penalty:} & 42.0 \\
        score_match = re.search(r'\\textbf\{Points Before Penalty:\}\s*&\s*([\d.]+)', tex_content)
        max_score_match = re.search(r'\\textbf\{Maximum Possible:\}\s*&\s*([\d.]+)', tex_content)

        if not score_match:
            # Try alternative: look at total in summary table
            # \rowcolor{ta_gray} \textbf{TOTAL} & \textbf{11.0} & - \\
            total_match = re.search(r'\\rowcolor\{ta_gray\}\s*\\textbf\{TOTAL\}\s*&\s*\\textbf\{([\d.]+)\}', tex_content)
            if total_match:
                total_score = float(total_match.group(1))
            else:
                print(f"⚠️  {student_name}: Could not extract score from TeX, skipping")
                continue
        else:
            total_score = float(score_match.group(1))

        # Get max score (default to 52 if not found)
        if max_score_match:
            max_score = float(max_score_match.group(1))
        else:
            max_score = 52.0  # Default for this assignment

        # Recalculate penalty with later timestamp
        new_penalty = reporter.calculate_late_penalty(
            submission_created_at=adj['later_time_str'],
            total_score=total_score,
            max_score=max_score
        )

        # Check if penalty actually changed
        old_penalty_match = re.search(r'\\textbf\{Days Late:\}\s*&\s*(\d+)\s*day', tex_content)
        old_days_late = int(old_penalty_match.group(1)) if old_penalty_match else 0

        if new_penalty['days_late'] == old_days_late:
            continue  # No change needed

        # Update TeX file with new penalty
        # The challenge: old PDFs may not have the "% --- LATE PENALTY WARNING" comment
        # So we need to insert the penalty section between \maketitle and % --- SECTION 1

        # First, try to find and replace existing late penalty section
        late_section_pattern = r'% --- LATE PENALTY WARNING.*?(?=% --- SECTION 1:)'

        if new_penalty['is_late']:
            new_late_section = f"""% --- LATE PENALTY WARNING (retroactively added) ---
\\begin{{center}}
\\fcolorbox{{late_red}}{{late_bg}}{{
    \\parbox{{0.90\\linewidth}}{{
        \\vspace{{0.5em}}
        \\centering
        \\textcolor{{late_red}}{{\\textbf{{\\Large LATE SUBMISSION PENALTY}}}}

        \\vspace{{0.3em}}
        \\hrule
        \\vspace{{0.3em}}

        \\begin{{tabular}}{{rl}}
        \\textbf{{Days Late:}} & {new_penalty['days_late']} day(s) \\\\
        \\textbf{{Penalty Rate:}} & {new_penalty['penalty_percentage']*100:.1f}\\% ({config.get('late_penalty', {}).get('percentage_per_day', 20)}\\% per day) \\\\
        \\textbf{{Points Before Penalty:}} & {total_score:.1f} \\\\
        \\textbf{{Points Deducted:}} & {new_penalty['points_deducted']:.1f} \\\\
        \\hline
        \\textbf{{Final Score:}} & \\textbf{{{new_penalty['final_score']:.1f}}} \\\\
        \\end{{tabular}}

        \\vspace{{0.5em}}
    }}
}}
\\end{{center}}

\\vspace{{0.5cm}}

"""
        else:
            new_late_section = ""

        # Escape backslashes in replacement string to prevent regex interpretation
        escaped_replacement = new_late_section.replace('\\', r'\\')

        # Try to replace existing late penalty section
        updated_tex, num_subs = re.subn(late_section_pattern, escaped_replacement, tex_content, flags=re.DOTALL)

        # If no existing section found, insert before SECTION 1
        if num_subs == 0 and new_penalty['is_late']:
            # Insert the penalty section right before "% --- SECTION 1"
            # Use a lambda to avoid backreference issues
            insert_pattern = r'(% --- SECTION 1:)'
            updated_tex = re.sub(insert_pattern, lambda m: new_late_section + m.group(1), tex_content)

        # Write updated TeX
        with open(tex_log_path, 'w') as f:
            f.write(updated_tex)

        # Recompile PDF
        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", f"Feedback_{safe_name}.tex"],
                cwd=output_dir / "logs",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30
            )

            # Move PDF to output directory
            compiled_pdf = output_dir / "logs" / f"Feedback_{safe_name}.pdf"
            if compiled_pdf.exists():
                compiled_pdf.rename(feedback_pdf_path)
                fixed_count += 1
                print(f"✅ {student_name}: Updated penalty ({old_days_late} → {new_penalty['days_late']} days late)")
            else:
                print(f"❌ {student_name}: PDF compilation failed")

        except Exception as e:
            print(f"❌ {student_name}: Error recompiling PDF: {e}")

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"✅ Successfully updated {fixed_count} out of {len(adjustments_needed)} feedback PDFs")
    print()


def create_placeholder_page(student_name: str, student_sid: str) -> bytes:
    """
    Creates a placeholder PDF page for students who submitted PDF but no TeX file.

    Args:
        student_name: Student's name
        student_sid: Student's SID

    Returns:
        PDF bytes for the placeholder page
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(width / 2, height - 100, "NO TEX SUBMISSION")

    # Student info
    c.setFont("Helvetica", 16)
    c.drawCentredString(width / 2, height - 150, f"Student: {student_name}")
    c.drawCentredString(width / 2, height - 180, f"SID: {student_sid}")

    # Instructions box
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, height - 250, "⚠️  INSTRUCTIONS FOR TA  ⚠️")

    c.setFont("Helvetica", 12)
    instructions = [
        "This student submitted a PDF to Gradescope but did NOT submit a",
        "TeX file for automated grading.",
        "",
        "ACTION REQUIRED:",
        "• Enter 0 points for this student in Gradescope",
        "• Move to the next submission",
        "",
        "The student will need to resubmit their TeX file if they want credit."
    ]

    y = height - 290
    for line in instructions:
        c.drawCentredString(width / 2, y, line)
        y -= 20

    # Draw border around instructions
    c.setStrokeColorRGB(0.8, 0, 0)
    c.setLineWidth(2)
    c.rect(50, height - 420, width - 100, 180)

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.getvalue()


def split_for_tas(config_path: str = "config.yaml", num_tas: int = 3):
    """
    Creates TA-specific PDFs containing first pages of feedback reports.

    Args:
        config_path: Path to config.yaml
        num_tas: Number of TAs to split work among
    """
    print("=" * 80)
    print("SPLIT ASSIGNMENTS FOR TA GRADESCOPE ENTRY")
    print("=" * 80)
    print()

    # Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    assignment_name = config['grading']['assignment_name']
    output_dir = Path(f"data/output/{assignment_name}_Feedback")

    # Detect corrections mode
    is_corrections_mode = assignment_name.endswith('_CORRECTIONS')
    if is_corrections_mode:
        base_assignment = assignment_name.replace('_CORRECTIONS', '')
        corrections_input_dir = Path(config['paths']['export_dir'])
        print(f"🔄 CORRECTIONS MODE detected")
        print(f"   Base assignment: {base_assignment}")
        print()

    if not output_dir.exists():
        print(f"❌ Output directory not found: {output_dir}")
        print("   Make sure you've run grade.py first!")
        return

    # Load TeX submissions to get timestamps and names
    ingestor = GradescopeIngestor(
        config['paths']['export_dir'],
        anonymize=config['grading']['anonymize']
    )
    ingest_result = ingestor.load()

    # Check if PDF submission metadata exists (for ordering)
    export_dir = Path(config['paths']['export_dir'])
    pdf_metadata_path = export_dir / 'pdf_submission_metadata.yml'

    use_pdf_ordering = pdf_metadata_path.exists()

    if use_pdf_ordering:
        print("📄 Found PDF submission metadata - using PDF submission order for TA splits")
        with open(pdf_metadata_path, 'r') as f:
            pdf_metadata = yaml.safe_load(f)
    else:
        print("📝 Using TeX submission order for TA splits (no PDF metadata found)")
        pdf_metadata = None

    # Create mapping of student name -> submission info
    # This uses TeX submission data for student info (names, SIDs)
    student_info = {}
    for student in ingest_result.submissions:
        safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")
        student_info[safe_name] = {
            'name': student.student_name,
            'created_at': student.created_at,  # TeX submission timestamp
            'sid': student.student_sid
        }

    # Find all feedback PDFs
    all_pdf_files = list(output_dir.glob("Feedback_*.pdf"))

    if not all_pdf_files:
        print(f"❌ No feedback PDFs found in {output_dir}")
        return

    # De-duplicate: For each student, keep only the latest version (or non-versioned if no versions)
    student_pdfs = {}
    for pdf_path in all_pdf_files:
        filename = pdf_path.stem  # e.g., "Feedback_John_Smith" or "Feedback_John_Smith_v2"
        student_key = filename.replace("Feedback_", "")

        # Handle versioned files (e.g., "_v2")
        version = 0
        if "_v" in student_key and student_key.split("_v")[-1].isdigit():
            base_key = "_".join(student_key.split("_v")[:-1])
            version = int(student_key.split("_v")[-1])
            student_key = base_key

        # Keep the latest version for each student
        if student_key not in student_pdfs or version > student_pdfs[student_key]['version']:
            student_pdfs[student_key] = {
                'path': pdf_path,
                'version': version
            }

    pdf_files = [info['path'] for info in student_pdfs.values()]
    print(f"📊 Found {len(pdf_files)} unique feedback PDFs (after removing duplicates)")

    # Build evaluation order based on PDF or TeX submissions
    if use_pdf_ordering:
        # Use PDF submission metadata for ordering
        pdf_submissions = []

        # Parse PDF metadata to extract submissions
        for submission_id, sub_data in pdf_metadata.items():
            if not sub_data or ':submitters' not in sub_data:
                continue

            submitter = sub_data[':submitters'][0]
            student_name = submitter[':name']
            student_sid = submitter[':sid']
            created_at = sub_data[':created_at']

            # Create safe name for matching
            safe_name = student_name.replace(" ", "_").replace("(", "").replace(")", "")

            # Check if we have a graded feedback PDF for this student
            has_feedback = safe_name in {info['path'].stem.replace("Feedback_", "") for info in student_pdfs.values()}

            try:
                # Handle both datetime objects and strings
                if isinstance(created_at, datetime):
                    timestamp = created_at
                else:
                    timestamp = datetime.strptime(created_at.split('.')[0], "%Y-%m-%d %H:%M:%S")

                pdf_submissions.append({
                    'student_name': student_name,
                    'safe_name': safe_name,
                    'sid': student_sid,
                    'timestamp': timestamp,
                    'has_feedback': has_feedback
                })
            except Exception as e:
                print(f"⚠️  Warning: Could not parse PDF timestamp for {student_name}: {e}")

        # Sort by PDF submission timestamp (earliest first)
        pdf_submissions.sort(key=lambda x: x['timestamp'])

        print(f"📄 Found {len(pdf_submissions)} PDF submissions")
        print(f"✅ {len([s for s in pdf_submissions if s['has_feedback']])} have graded feedback")

        # Different behavior for corrections vs normal mode
        if is_corrections_mode:
            print(f"🔄 CORRECTIONS: Only including students with .tex submissions (no placeholders)")
            # Only include students who have feedback (submitted .tex)
            pdf_with_timestamps = []
            for sub in pdf_submissions:
                if sub['has_feedback']:
                    pdf_path = student_pdfs[sub['safe_name']]['path']
                    pdf_with_timestamps.append({
                        'path': pdf_path,
                        'timestamp': sub['timestamp'],
                        'student_name': sub['student_name'],
                        'sid': sub['sid'],
                        'has_feedback': True
                    })
        else:
            print(f"⚠️  {len([s for s in pdf_submissions if not s['has_feedback']])} missing feedback (will show placeholder)")
            # Normal mode: include placeholders for missing feedback
            pdf_with_timestamps = []
            for sub in pdf_submissions:
                if sub['has_feedback']:
                    # Find the feedback PDF
                    pdf_path = student_pdfs[sub['safe_name']]['path']
                    pdf_with_timestamps.append({
                        'path': pdf_path,
                        'timestamp': sub['timestamp'],
                        'student_name': sub['student_name'],
                        'sid': sub['sid'],
                        'has_feedback': True
                    })
                else:
                    # Placeholder for missing feedback
                    pdf_with_timestamps.append({
                        'path': None,
                        'timestamp': sub['timestamp'],
                        'student_name': sub['student_name'],
                        'sid': sub['sid'],
                        'has_feedback': False
                    })

        total_submissions = len(pdf_with_timestamps)
        print(f"✅ Sorted {total_submissions} submissions by PDF submission timestamp")

    else:
        # Use TeX submission timestamps for ordering (original behavior)
        pdf_with_timestamps = []
        missing_timestamp = []

        for pdf_path in pdf_files:
            # Extract student name from filename
            filename = pdf_path.stem
            student_key = filename.replace("Feedback_", "")

            # Handle versioned files (e.g., "_v2")
            if "_v" in student_key and student_key.split("_v")[-1].isdigit():
                student_key = "_".join(student_key.split("_v")[:-1])

            if student_key in student_info:
                info = student_info[student_key]
                try:
                    # Parse timestamp
                    timestamp_str = info['created_at']
                    if timestamp_str and timestamp_str != "Unknown":
                        timestamp = datetime.strptime(timestamp_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
                        pdf_with_timestamps.append({
                            'path': pdf_path,
                            'timestamp': timestamp,
                            'student_name': info['name'],
                            'sid': info['sid'],
                            'has_feedback': True
                        })
                    else:
                        missing_timestamp.append(pdf_path)
                except Exception as e:
                    print(f"⚠️  Warning: Could not parse timestamp for {info['name']}: {e}")
                    missing_timestamp.append(pdf_path)
            else:
                print(f"⚠️  Warning: Could not find submission info for {pdf_path.name}")
                missing_timestamp.append(pdf_path)

        # Sort by timestamp (earliest first)
        pdf_with_timestamps.sort(key=lambda x: x['timestamp'])

        # Add missing timestamp files at the end
        for pdf_path in missing_timestamp:
            pdf_with_timestamps.append({
                'path': pdf_path,
                'timestamp': None,
                'student_name': 'Unknown',
                'sid': 'Unknown',
                'has_feedback': True
            })

        total_submissions = len(pdf_with_timestamps)
        print(f"✅ Sorted {total_submissions} submissions by TeX submission timestamp")

    print()

    # Calculate split
    base_count = total_submissions // num_tas
    remainder = total_submissions % num_tas

    ta_splits = []
    start_idx = 0
    for i in range(num_tas):
        # Distribute remainder among first TAs
        count = base_count + (1 if i < remainder else 0)
        end_idx = start_idx + count
        ta_splits.append({
            'ta_number': i + 1,
            'start': start_idx,
            'end': end_idx,
            'count': count
        })
        start_idx = end_idx

    # Display split
    print("📋 Assignment split:")
    for split in ta_splits:
        print(f"   TA {split['ta_number']}: {split['count']} submissions (indices {split['start']}-{split['end']-1})")
    print()

    # Create TA PDFs
    ta_pdfs_dir = output_dir / "TA_Splits"
    ta_pdfs_dir.mkdir(exist_ok=True)

    print("📄 Creating TA PDF files...")
    print()

    for split in ta_splits:
        ta_num = split['ta_number']
        writer = PdfWriter()

        # Get submissions for this TA
        ta_submissions = pdf_with_timestamps[split['start']:split['end']]

        # Extract first page from each PDF (or add placeholder)
        for idx, submission in enumerate(ta_submissions, start=1):
            pdf_path = submission['path']
            student_name = submission['student_name']
            student_sid = submission['sid']
            timestamp = submission['timestamp']
            has_feedback = submission.get('has_feedback', True)

            try:
                if not has_feedback or pdf_path is None:
                    # Student submitted PDF but no TeX - add placeholder page
                    placeholder_pdf = create_placeholder_page(student_name, student_sid)
                    placeholder_reader = PdfReader(io.BytesIO(placeholder_pdf))
                    writer.add_page(placeholder_reader.pages[0])
                else:
                    # Normal case: add first page from feedback PDF
                    # Use append() with page range to avoid page reference issues
                    # This prevents PyPDF2 from reusing page objects from previous readers
                    if len(PdfReader(pdf_path).pages) > 0:
                        # Append only first page (pages=(0, 1) means page index 0, end is exclusive)
                        writer.append(fileobj=pdf_path, pages=(0, 1))
                    else:
                        print(f"   ⚠️  {pdf_path.name} has no pages!")
            except Exception as e:
                if has_feedback:
                    print(f"   ❌ Error reading {pdf_path.name}: {e}")
                else:
                    print(f"   ❌ Error creating placeholder for {student_name}: {e}")

        # Write TA PDF
        ta_pdf_path = ta_pdfs_dir / f"TA{ta_num}_Gradescope_Entry_{assignment_name}.pdf"
        with open(ta_pdf_path, 'wb') as f:
            writer.write(f)

        print(f"✅ TA {ta_num}: {ta_pdf_path.name} ({split['count']} pages)")

    # Create index file for TAs
    index_path = ta_pdfs_dir / "TA_Assignment_Index.txt"
    with open(index_path, 'w') as f:
        f.write(f"TA Assignment Index - {assignment_name}\n")
        f.write("=" * 80 + "\n\n")

        for split in ta_splits:
            f.write(f"TA {split['ta_number']} ({split['count']} submissions)\n")
            f.write("-" * 40 + "\n")

            ta_submissions = pdf_with_timestamps[split['start']:split['end']]
            for idx, submission in enumerate(ta_submissions, start=1):
                timestamp_str = submission['timestamp'].strftime("%Y-%m-%d %H:%M:%S") if submission['timestamp'] else "Unknown"
                has_feedback = submission.get('has_feedback', True)
                status_marker = "" if has_feedback else " [NO TEX SUBMISSION - ENTER 0]"
                f.write(f"{idx:3d}. {submission['student_name']:30s} (SID: {submission['sid']:12s}) - {timestamp_str}{status_marker}\n")

            f.write("\n")

    print(f"\n📋 Created index file: {index_path.name}")
    print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"📂 Output directory: {ta_pdfs_dir}")
    print(f"📊 Total submissions: {total_submissions}")
    print(f"👥 Number of TAs: {num_tas}")
    print(f"📄 Files created:")
    for split in ta_splits:
        print(f"   - TA{split['ta_number']}_Gradescope_Entry_{assignment_name}.pdf ({split['count']} pages)")
    print(f"   - TA_Assignment_Index.txt")
    print()
    print("🎉 Done! TAs can now use these PDFs for Gradescope entry.")
    print()
    print("📝 Instructions for TAs:")
    print("   1. Each TA receives ONE PDF with first pages (summary pages) only")
    print("   2. Pages are in CHRONOLOGICAL ORDER (earliest submissions first)")
    print("   3. Use the index file to see which students each TA is responsible for")
    print("   4. Enter scores into Gradescope in the same order as the PDF pages")


def send_feedback(config_path: str = "config.yaml",
                 username: str = None,
                 dry_run: bool = False,
                 test_recipients: List[str] = None,
                 force_resend: bool = False):
    """
    Send feedback PDFs to students via email.

    Args:
        config_path: Path to config.yaml
        username: SMTP username (prompts if not provided)
        dry_run: If True, simulate sending without actual emails
        test_recipients: List of email addresses to send to (testing mode)
        force_resend: If True, resend even if already delivered
    """
    print("=" * 80)
    print("SEND FEEDBACK EMAILS")
    print("=" * 80)
    print()

    # 1. Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    assignment_name = config['grading']['assignment_name']
    output_dir = Path(f"data/output/{assignment_name}_Feedback")

    # Detect corrections mode
    is_corrections_mode = assignment_name.endswith('_CORRECTIONS')
    if is_corrections_mode:
        base_assignment = assignment_name.replace('_CORRECTIONS', '')
        print(f"🔄 CORRECTIONS MODE detected")
        print(f"   Base assignment: {base_assignment}")
        print()

    if not output_dir.exists():
        print(f"❌ Output directory not found: {output_dir}")
        print("   Make sure you've run grade.py first!")
        return

    # 2. Load submissions to get student emails
    print("📂 Loading student submissions...")
    ingestor = GradescopeIngestor(
        config['paths']['export_dir'],
        anonymize=config['grading']['anonymize']
    )
    ingest_result = ingestor.load()

    # 3. Initialize emailer (with corrections mode info)
    emailer = FeedbackEmailer(
        config_path=config_path,
        output_dir=str(output_dir),
        is_corrections_mode=is_corrections_mode
    )

    # 4. Authenticate (skip if dry-run)
    if not dry_run:
        try:
            # Use appropriate authentication based on backend
            if emailer.email_backend == 'applescript':
                emailer.authenticate_applescript()
            else:
                emailer.authenticate(username=username)
        except Exception as e:
            print(f"\n❌ Authentication failed. Cannot proceed.")
            return

    # 5. Send emails
    emailer.send_feedback_batch(
        submissions=ingest_result.submissions,
        dry_run=dry_run,
        test_recipients=test_recipients,
        force_resend=force_resend
    )

    # 6. Display delivery report
    report = emailer.get_delivery_report()
    print()
    print("=" * 80)
    print("EMAIL DELIVERY REPORT")
    print("=" * 80)
    print(f"Total Students:    {report['total']}")
    print(f"Successfully Sent: {report['sent']}")
    print(f"Failed:            {report['failed']}")

    if report['failed'] > 0:
        print()
        print("Failed Deliveries:")
        for failure in report['failures']:
            print(f"  - {failure['email']}: {failure['error']} ({failure['attempts']} attempts)")

    print()
    print(f"Details saved to: {output_dir}/email_delivery.yaml")
    print()

    if dry_run:
        print("🧪 DRY-RUN MODE: No emails were actually sent.")
        print("   Remove --dry-run flag to send emails for real.")
    elif test_recipients:
        print("🧪 TEST MODE: Emails sent only to specified test recipients.")
        print("   Remove --test-recipients to send to all students.")


def audit_reports(config_path: str = "config.yaml"):
    """
    Audit feedback reports for common issues:
    1. Rubric item name mismatches (LLM fabricated/modified criterion names)
    2. Point total inconsistencies (deductions don't sum correctly)
    3. LaTeX compilation failures (.tex files left in main output directory)
    4. Missing feedback sections (expected sections from rubric not present)
    5. Duplicate criterion names within a section

    Outputs a list of student names suitable for grade.py --regrade.
    """
    print("=" * 80)
    print("AUDIT FEEDBACK REPORTS")
    print("=" * 80)
    print()

    # Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    assignment_name = config['grading']['assignment_name']
    is_corrections_mode = assignment_name.endswith('_CORRECTIONS')
    output_dir = Path(f"data/output/{assignment_name}_Feedback")
    logs_dir = output_dir / "logs"
    rubric_path = Path(config['paths']['rubrics_dir']) / config['paths']['rubric_file']

    if not output_dir.exists():
        print(f"❌ Output directory not found: {output_dir}")
        return

    if not rubric_path.exists():
        print(f"❌ Rubric file not found: {rubric_path}")
        return

    # Load rubric
    with open(rubric_path, 'r') as f:
        rubric = yaml.safe_load(f)

    # Build rubric reference: { "Problem X (y)": { "names": [list of criterion names], "points": {name: pts}, "max": total_pts } }
    rubric_ref = {}
    for problem_label, sub_problems in rubric.items():
        for section in sub_problems:
            title = section['title']
            items = section.get('items', [])
            names = []
            points = {}
            max_pts = 0
            ec_max = 0
            for item in items:
                names.append(item['name'])
                points[item['name']] = item['points']
                if item.get('is_extra_credit', False):
                    ec_max += item['points']
                else:
                    max_pts += item['points']
            rubric_ref[title] = {
                'names': names,
                'points': points,
                'max': max_pts,
                'ec_max': ec_max,
                'items': items
            }

    print(f"📋 Loaded rubric: {rubric_path.name}")
    print(f"   Sections: {', '.join(rubric_ref.keys())}")
    print()

    # Collect all issues
    flagged_students = {}  # student_name -> list of issues

    def flag(student_name, issue):
        if student_name not in flagged_students:
            flagged_students[student_name] = []
        flagged_students[student_name].append(issue)

    # --- CHECK 1: LaTeX compilation failures ---
    print("🔍 Check 1: LaTeX compilation failures...")
    tex_in_main = list(output_dir.glob("Feedback_*.tex"))
    compilation_failures = 0
    for tex_file in tex_in_main:
        student_name = tex_file.stem.replace("Feedback_", "").replace("_", " ")
        flag(student_name, f"LaTeX compilation failure (.tex file in output dir)")
        compilation_failures += 1

    if compilation_failures > 0:
        print(f"   ⚠️  Found {compilation_failures} compilation failures")
    else:
        print(f"   ✅ No compilation failures detected")
    print()

    # --- CHECK 2 & 3: Rubric item names and point totals ---
    print("🔍 Check 2: Rubric item name mismatches...")
    print("🔍 Check 3: Point total validation...")
    print("🔍 Check 4: Missing feedback sections...")
    print("🔍 Check 5: Duplicate criterion names...")

    # Identify which sections are pure extra credit (max=0, all items are EC)
    pure_ec_sections = set()
    for section_title, section_data in rubric_ref.items():
        if section_data['max'] == 0 and section_data['ec_max'] > 0:
            pure_ec_sections.add(section_title)

    if pure_ec_sections:
        print(f"   ℹ️  Pure extra credit sections (excluded from point checks): {', '.join(pure_ec_sections)}")
        print()

    # Process all .tex files from logs directory AND main output directory (compilation failures)
    tex_files = list(logs_dir.glob("Feedback_*.tex"))
    # Also include .tex files from main output dir that aren't in logs
    logged_names = {f.name for f in tex_files}
    for tex_file in output_dir.glob("Feedback_*.tex"):
        if tex_file.name not in logged_names:
            tex_files.append(tex_file)

    if not tex_files:
        print(f"   ❌ No .tex files found in {logs_dir}")
        print()
    else:
        print(f"   📂 Processing {len(tex_files)} feedback reports...")
        print()

        name_mismatch_count = 0
        point_error_count = 0
        missing_section_count = 0
        duplicate_criterion_count = 0

        for tex_file in tex_files:
            student_safe_name = tex_file.stem.replace("Feedback_", "")
            student_name = student_safe_name.replace("_", " ")

            with open(tex_file, 'r', encoding='utf-8', errors='replace') as f:
                tex_content = f.read()

            # === Parse summary table ===
            # Pattern: "Problem X" or "Problem X (y)" followed by table columns
            # The sub-letter (a), (b), etc. is optional to support both "Problem 1" and "Problem 1 (a)" formats
            problem_pattern = r'Problem\s+\d+(?:\s*\([a-z]\))?'
            table_rows = re.findall(
                r'^(' + problem_pattern + r')\s*&\s*\\textbf\{([\d.]+)\}\s*&\s*([\d.]+)\s*&\s*'
                r'(?:---|\s*\\textcolor\{extra_credit_green\}\{\+([\d.]+)\}\s*)\s*&\s*'
                r'\\textcolor\{late_red\}\{-([\d.]+)\}',
                tex_content, re.MULTILINE
            )

            # Parse total row
            total_match = re.search(
                r'\\textbf\{TOTAL\}\s*&\s*\\textbf\{([\d.]+)\}\s*&\s*([\d.]+)',
                tex_content
            )

            table_scores = {}  # "Problem X (y)" -> { score, max, ec, deducted }
            for row in table_rows:
                section_name = row[0].strip()
                score = float(row[1])
                max_pts = float(row[2])
                ec = float(row[3]) if row[3] else 0.0
                deducted = float(row[4])
                table_scores[section_name] = {
                    'score': score,
                    'max': max_pts,
                    'ec': ec,
                    'deducted': deducted
                }

            # === Parse feedback sections ===
            # Extract "Incorrect Rubric Items" sections
            incorrect_sections = re.findall(
                r'\\subsection\*\{(' + problem_pattern + r')\s*Incorrect Rubric Items\s*\\hfill\s*Score:\s*\[([\d.]+)/([\d.]+)\]\}',
                tex_content
            )

            # Extract "Extra Credit" sections
            ec_sections = re.findall(
                r'\\subsection\*\{(' + problem_pattern + r')\s*Extra Credit\s*\\hfill',
                tex_content
            )

            # === Parse individual rubric items ===
            # Find all \item \textbf{NAME} (\textcolor{late_red}{Missed X.X pts})
            # and \item \textbf{NAME} (\textcolor{extra_credit_green}{+X.X pts})
            all_items = re.findall(
                r'\\item\s*\\textbf\{(.+?)\}\s*\(\\textcolor\{(?:late_red|extra_credit_green)\}\{(?:Missed\s+|[+])([\d.]+)\s*pts?\}\)',
                tex_content
            )

            # === CHECK 2: Rubric item name matching ===
            # Reverse the LaTeX escaping to compare with rubric names
            for item_name_escaped, pts_str in all_items:
                # Reverse common LaTeX escapes
                item_name = item_name_escaped
                item_name = item_name.replace(r'\_', '_')
                item_name = item_name.replace(r'\&', '&')
                item_name = item_name.replace(r'\%', '%')
                item_name = item_name.replace(r'\#', '#')
                item_name = item_name.replace(r'\{', '{')
                item_name = item_name.replace(r'\}', '}')
                item_name = item_name.replace(r'\textbackslash{}', '\\')
                # Remove trailing periods and whitespace for flexible matching
                item_name_clean = item_name.strip().rstrip('.')

                # Check if this name matches any rubric item
                found_match = False
                for section_title, section_data in rubric_ref.items():
                    for rubric_name in section_data['names']:
                        rubric_name_clean = rubric_name.strip().rstrip('.')

                        # Exact match
                        if item_name_clean == rubric_name_clean:
                            found_match = True
                            break

                        # Check if rubric name starts with the item name (or vice versa)
                        # This catches cases where the LLM appended the description
                        if rubric_name_clean.startswith(item_name_clean) or item_name_clean.startswith(rubric_name_clean):
                            # If item_name is significantly longer, it's description bleed
                            if len(item_name_clean) > len(rubric_name_clean) * 1.3:
                                flag(student_name, f"Criterion name bleed in '{section_title}': "
                                     f"'{item_name_clean[:120]}...' (rubric: '{rubric_name_clean[:120]}...')")
                                name_mismatch_count += 1
                            found_match = True
                            break

                    if found_match:
                        break

                if not found_match:
                    # In corrections mode, "Original Extra Credit (preserved)" is a
                    # legitimate synthetic criterion used to carry forward EC scores
                    if is_corrections_mode and "Original Extra Credit" in item_name_clean:
                        continue
                    # No match at all - LLM fabricated a criterion
                    flag(student_name, f"Fabricated/unknown criterion: '{item_name_clean[:80]}'")
                    name_mismatch_count += 1

            # === CHECK 3: Point total validation ===
            # Verify that score + deducted = max for each section in the table
            # Skip pure extra credit sections (score reflects EC earned, max=0 is by design)
            for section_name, scores in table_scores.items():
                if section_name in pure_ec_sections:
                    continue  # EC sections have score > 0 but max=0, which is expected
                expected_score = scores['max'] - scores['deducted']
                if abs(scores['score'] - expected_score) > 0.01:
                    flag(student_name, f"Point mismatch in table for '{section_name}': "
                         f"score={scores['score']}, max={scores['max']}, "
                         f"deducted={scores['deducted']} (expected score={expected_score})")
                    point_error_count += 1

            # Verify total row sums
            if total_match and table_scores:
                total_score = float(total_match.group(1))
                total_max = float(total_match.group(2))
                sum_scores = sum(s['score'] for s in table_scores.values())
                sum_max = sum(s['max'] for s in table_scores.values())

                if abs(total_score - sum_scores) > 0.01:
                    flag(student_name, f"Total score mismatch: TOTAL={total_score} but sum of sections={sum_scores}")
                    point_error_count += 1

                if abs(total_max - sum_max) > 0.01:
                    flag(student_name, f"Total max mismatch: TOTAL max={total_max} but sum of sections={sum_max}")
                    point_error_count += 1

            # Cross-check feedback section scores with table scores
            for section_name, fb_score_str, fb_max_str in incorrect_sections:
                section_name = section_name.strip()
                fb_score = float(fb_score_str)
                fb_max = float(fb_max_str)

                if section_name in table_scores:
                    ts = table_scores[section_name]
                    if abs(fb_score - ts['score']) > 0.01:
                        flag(student_name, f"Score mismatch between table and feedback for '{section_name}': "
                             f"table={ts['score']}, feedback={fb_score}")
                        point_error_count += 1
                    if abs(fb_max - ts['max']) > 0.01:
                        flag(student_name, f"Max mismatch between table and feedback for '{section_name}': "
                             f"table={ts['max']}, feedback={fb_max}")
                        point_error_count += 1

            # === CHECK 4: Missing feedback sections ===
            # Every section in the rubric should appear somewhere in the table
            # In corrections mode, pure EC sections may legitimately be absent
            # (only corrected sections are included in the report)
            sections_in_table = set(table_scores.keys())
            for section_title in rubric_ref.keys():
                if section_title not in sections_in_table:
                    if is_corrections_mode and section_title in pure_ec_sections:
                        continue  # EC sections are optional in corrections reports
                    flag(student_name, f"Missing section in summary table: '{section_title}'")
                    missing_section_count += 1

            # === CHECK 5: Duplicate criterion names within the same section ===
            # Extract items grouped by their parent section to check for duplicates
            # per-section (the same criterion name can legitimately appear in different problems)
            section_items = {}
            # Find each section and its items using positional matching
            section_headers = list(re.finditer(
                r'\\subsection\*\{(' + problem_pattern + r')\s*(?:Incorrect Rubric Items|Extra Credit)',
                tex_content
            ))
            for i, header_match in enumerate(section_headers):
                section_name = header_match.group(1).strip()
                start = header_match.end()
                end = section_headers[i + 1].start() if i + 1 < len(section_headers) else len(tex_content)
                section_text = tex_content[start:end]
                items_in_section = re.findall(
                    r'\\item\s*\\textbf\{(.+?)\}\s*\(\\textcolor',
                    section_text
                )
                seen_in_section = {}
                for item_key in items_in_section:
                    item_key = item_key.strip()
                    if item_key in seen_in_section:
                        flag(student_name, f"Duplicate criterion in '{section_name}': '{item_key[:60]}'")
                        duplicate_criterion_count += 1
                    else:
                        seen_in_section[item_key] = True

        print(f"   Rubric name mismatches: {name_mismatch_count}")
        print(f"   Point total errors: {point_error_count}")
        print(f"   Missing sections: {missing_section_count}")
        print(f"   Duplicate criteria: {duplicate_criterion_count}")
        print()

    # === RESULTS ===
    print("=" * 80)
    print("AUDIT RESULTS")
    print("=" * 80)
    print()

    if not flagged_students:
        print("✅ No issues found! All feedback reports passed audit checks.")
        return

    print(f"⚠️  Found issues in {len(flagged_students)} student reports:\n")

    for student_name in sorted(flagged_students.keys()):
        issues = flagged_students[student_name]
        print(f"  📋 {student_name} ({len(issues)} issue{'s' if len(issues) > 1 else ''}):")
        for issue in issues:
            print(f"      • {issue}")
        print()

    # Separate students into categories:
    # 1. Students with non-compilation issues (rubric mismatches, point errors, etc.)
    # 2. Students with ONLY compilation failures
    tex_failure_tag = "LaTeX compilation failure"
    non_compilation_students = []
    compilation_only_students = []

    for student_name in sorted(flagged_students.keys()):
        issues = flagged_students[student_name]
        has_non_compilation = any(tex_failure_tag not in issue for issue in issues)
        if has_non_compilation:
            non_compilation_students.append(student_name)
        else:
            compilation_only_students.append(student_name)

    # Generate regrade commands
    print("=" * 80)
    print("REGRADE COMMANDS")
    print("=" * 80)
    print()

    export_dir = config['paths']['export_dir']

    if non_compilation_students:
        non_comp_args = ' '.join(f'"{name}"' for name in non_compilation_students)
        print(f"📌 Regrade students with rubric/point issues ({len(non_compilation_students)} students):")
        print()
        print(f"  uv run python grade.py {export_dir} --regrade {non_comp_args}")
        print()

    if compilation_only_students:
        comp_args = ' '.join(f'"{name}"' for name in compilation_only_students)
        print(f"📎 Regrade students with ONLY compilation failures ({len(compilation_only_students)} students):")
        print()
        print(f"  uv run python grade.py {export_dir} --regrade {comp_args}")
        print()

    all_names = sorted(flagged_students.keys())
    if len(all_names) > 0:
        all_args = ' '.join(f'"{name}"' for name in all_names)
        print(f"📋 Regrade ALL flagged students ({len(all_names)} students):")
        print()
        print(f"  uv run python grade.py {export_dir} --regrade {all_args}")
        print()

    # Also save the list to a file for convenience
    regrade_list_path = output_dir / "audit_regrade_list.txt"
    with open(regrade_list_path, 'w') as f:
        f.write(f"# Audit regrade list - {assignment_name}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total: {len(all_names)} students\n")
        f.write(f"# Rubric/point issues: {len(non_compilation_students)}\n")
        f.write(f"# Compilation-only: {len(compilation_only_students)}\n\n")

        if non_compilation_students:
            f.write("# === RUBRIC/POINT ISSUES ===\n\n")
            for name in non_compilation_students:
                issues = flagged_students[name]
                f.write(f"{name}\n")
                for issue in issues:
                    f.write(f"  # {issue}\n")
                f.write("\n")

        if compilation_only_students:
            f.write("# === COMPILATION-ONLY FAILURES ===\n\n")
            for name in compilation_only_students:
                issues = flagged_students[name]
                f.write(f"{name}\n")
                for issue in issues:
                    f.write(f"  # {issue}\n")
                f.write("\n")

    print(f"📄 Regrade list saved to: {regrade_list_path}")
    print()


def main():

    parser = argparse.ArgumentParser(
        description="Post-grading workflow utilities for LaTA",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Post-grading task to perform')

    # Split for TAs command
    split_parser = subparsers.add_parser(
        'split-for-tas',
        help='Split feedback PDFs among TAs for Gradescope entry'
    )
    split_parser.add_argument(
        '--num-tas',
        type=int,
        default=3,
        help='Number of TAs to split work among (default: 3)'
    )
    split_parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to config file (default: config.yaml)'
    )

    # Finalize late penalties command
    finalize_parser = subparsers.add_parser(
        'finalize-late-penalties',
        help='Synchronize late penalties between TeX and PDF submissions (use later timestamp)'
    )
    finalize_parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to config file (default: config.yaml)'
    )

    # Send feedback command
    send_parser = subparsers.add_parser(
        'send-feedback',
        help='Send feedback PDFs to students via email'
    )
    send_parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to config file (default: config.yaml)'
    )
    send_parser.add_argument(
        '--username',
        type=str,
        help='SMTP username (prompts if omitted)'
    )
    send_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate sending without actual emails'
    )
    send_parser.add_argument(
        '--test-recipients',
        nargs='+',
        help='Send only to these email addresses (testing)'
    )
    send_parser.add_argument(
        '--force-resend',
        action='store_true',
        help='Resend even if already delivered'
    )

    # Audit reports command
    audit_parser = subparsers.add_parser(
        'audit',
        help='Audit feedback reports for rubric mismatches, point errors, and compilation failures'
    )
    audit_parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to config file (default: config.yaml)'
    )

    args = parser.parse_args()

    if args.command == 'split-for-tas':
        split_for_tas(config_path=args.config, num_tas=args.num_tas)
    elif args.command == 'finalize-late-penalties':
        finalize_late_penalties(config_path=args.config)
    elif args.command == 'send-feedback':
        send_feedback(
            config_path=args.config,
            username=args.username,
            dry_run=args.dry_run,
            test_recipients=args.test_recipients,
            force_resend=args.force_resend
        )
    elif args.command == 'audit':
        audit_reports(config_path=args.config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
