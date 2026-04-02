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

"""Retroactive Metadata Extractor

Extracts late_penalty and zero_scored_items from existing feedback .tex files
and updates grading_metadata.yaml for past assignments (HW1, HW2).

Usage:
    python retroactive_metadata.py ME373_HW1
    python retroactive_metadata.py ME373_HW2
"""

import re
import yaml
import sys
from pathlib import Path
from typing import List


def extract_late_penalty(tex_content: str) -> float:
    """
    Extract late penalty percentage from LaTeX feedback file.

    Looks for line like:
        \\textbf{Penalty Rate:} & 20.0\\% (20\\% per day) \\

    Returns penalty as decimal (e.g., 0.2 for 20%)
    """
    # Pattern: \textbf{Penalty Rate:} & 20.0\% (20\% per day) \\
    pattern = r'\\textbf\{Penalty Rate:\}\s*&\s*([0-9.]+)\\%'
    match = re.search(pattern, tex_content)

    if match:
        penalty_percent = float(match.group(1))
        return penalty_percent / 100.0  # Convert to decimal

    return 0.0  # No late penalty found


def extract_zero_items(tex_content: str) -> List[str]:
    """
    Extract zero-scored items from grading summary table.

    Looks for lines like:
        Problem 1 (a) & \\textbf{0.0} & 6.0 & --- & \\textcolor{late_red}{-6.0} \\
        Problem 3 & \\textbf{0.0} & 30.0 & --- & \\textcolor{late_red}{-30.0} \\

    Returns list like ["1a", "3"]
    """
    zero_items = []

    # Pattern: Problem X (Y) & \textbf{0.0} & ...
    # Captures problem number and optional criterion letter
    pattern = r'Problem\s+(\d+)\s*(?:\(([a-z])\))?\s*&\s*\\textbf\{0\.0\}'

    for match in re.finditer(pattern, tex_content):
        problem_num = match.group(1)
        criterion_letter = match.group(2)  # May be None

        if criterion_letter:
            zero_items.append(f"{problem_num}{criterion_letter}")
        else:
            zero_items.append(problem_num)

    return zero_items


def extract_extra_credit(tex_content: str) -> dict:
    """
    Extract extra credit awarded from feedback .tex file.

    Looks for patterns in summary table:
        Problem 1 (b) & \\textbf{8.0} & 6.0 & \\textcolor{extra_credit_green}{+2.0} & ---
        Problem 3 & \\textbf{10.0} & 9.0 & \\textcolor{extra_credit_green}{+1.0} & ---

    Returns dict mapping problem_group -> points:
        {"Problem 1 (b)": 2.0, "Problem 3": 1.0}
    """
    extra_credit_earned = {}

    # Pattern: Problem X (optional letter) & score & max & extra_credit & deducted
    # We want to capture the problem identifier and the extra credit amount
    pattern = r'Problem\s+(\d+)\s*(?:\(([a-z])\))?\s*&\s*\\textbf\{[\d.]+\}\s*&\s*[\d.]+\s*&\s*\\textcolor\{extra_credit_green\}\{\+\s*([\d.]+)\}'

    for match in re.finditer(pattern, tex_content):
        problem_num = match.group(1)
        criterion_letter = match.group(2)  # May be None
        extra_points = float(match.group(3))

        # Build problem_group identifier
        if criterion_letter:
            problem_group = f"Problem {problem_num} ({criterion_letter})"
        else:
            problem_group = f"Problem {problem_num}"

        extra_credit_earned[problem_group] = extra_points

    return extra_credit_earned


def extract_student_name(filename: str) -> str:
    """
    Extract student name from filename like 'Feedback_Benjamin_Stewart.tex'
    Returns: 'Benjamin_Stewart'
    """
    # Remove 'Feedback_' prefix and '.tex' suffix
    name = filename.replace('Feedback_', '').replace('.tex', '')

    # Remove '_broken' or '_attempt_N' suffixes if present
    name = re.sub(r'_broken.*$', '', name)
    name = re.sub(r'_attempt_\d+$', '', name)

    return name


def process_assignment(assignment_name: str):
    """Process all feedback files for an assignment and update metadata."""

    feedback_dir = Path(f"data/output/{assignment_name}_Feedback")
    logs_dir = feedback_dir / "logs"
    metadata_path = feedback_dir / "grading_metadata.yaml"

    if not feedback_dir.exists():
        print(f"❌ Error: Feedback directory not found: {feedback_dir}")
        return

    if not logs_dir.exists():
        print(f"❌ Error: Logs directory not found: {logs_dir}")
        return

    if not metadata_path.exists():
        print(f"❌ Error: Metadata file not found: {metadata_path}")
        return

    # Load existing metadata
    with open(metadata_path, 'r') as f:
        metadata = yaml.safe_load(f) or {}

    print(f"\n📋 Processing {assignment_name}")
    print(f"   Found {len(metadata)} students in metadata")

    # Get all feedback .tex files
    tex_files = list(logs_dir.glob("Feedback_*.tex"))

    # Filter out broken/attempt files, keep only main files
    main_tex_files = [f for f in tex_files if not ('_broken' in f.name or '_attempt_' in f.name)]

    print(f"   Found {len(main_tex_files)} feedback .tex files")

    updated_count = 0
    skipped_count = 0

    for tex_file in main_tex_files:
        student_name = extract_student_name(tex_file.name)

        # Find matching entry in metadata (metadata keys are student names)
        if student_name not in metadata:
            print(f"   ⚠️  Skipping {student_name}: not found in metadata")
            skipped_count += 1
            continue

        # Read tex file
        with open(tex_file, 'r', encoding='utf-8') as f:
            tex_content = f.read()

        # Extract data
        late_penalty = extract_late_penalty(tex_content)
        zero_scored_items = extract_zero_items(tex_content)
        extra_credit_earned = extract_extra_credit(tex_content)

        # Update metadata
        metadata[student_name]['late_penalty'] = late_penalty
        metadata[student_name]['zero_scored_items'] = zero_scored_items
        metadata[student_name]['extra_credit_earned'] = extra_credit_earned

        updated_count += 1

        if late_penalty > 0 or zero_scored_items or extra_credit_earned:
            print(f"   ✓ {student_name}: penalty={late_penalty*100:.0f}%, zeros={zero_scored_items}, extra_credit={extra_credit_earned}")

    # Save updated metadata
    with open(metadata_path, 'w') as f:
        yaml.safe_dump(metadata, f, default_flow_style=False, sort_keys=False)

    print(f"\n✅ Complete!")
    print(f"   Updated: {updated_count} students")
    print(f"   Skipped: {skipped_count} students")
    print(f"   Saved to: {metadata_path}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python retroactive_metadata.py <assignment_name>")
        print("Example: python retroactive_metadata.py ME373_HW1")
        sys.exit(1)

    assignment_name = sys.argv[1]
    process_assignment(assignment_name)


if __name__ == '__main__':
    main()
