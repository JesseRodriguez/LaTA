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
Validation and fixing module for LaTeX feedback before compilation.
Ensures rubric items have correct point values and sum to the correct total.
"""

import re
import yaml
from pathlib import Path

def load_rubric_structure(rubric_path):
    """Load rubric and create problem -> {criterion: points} mapping."""
    with open(rubric_path, 'r') as f:
        rubric = yaml.safe_load(f)

    problem_to_criteria = {}
    for problem, sections in rubric.items():
        for section in sections:
            section_title = section['title']
            criteria = {item['name']: float(item['points']) for item in section['items']}
            problem_to_criteria[section_title] = criteria

    return problem_to_criteria

def clean_criterion_name(tex_name):
    """Remove LaTeX escapes from criterion name for matching."""
    clean = tex_name.replace(r'\_', '_')
    clean = clean.replace(r'\textbackslash{}', '\\')
    clean = clean.replace(r'\&', '&')
    clean = clean.replace(r'\%', '%')
    clean = clean.replace(r'\#', '#')
    clean = clean.replace(r'\{', '{')
    clean = clean.replace(r'\}', '}')
    clean = clean.replace(r'\$', '$')
    clean = re.sub(r'\\texttt\{([^}]*)\}', r'\1', clean)
    return clean.strip()

def validate_and_fix_tex_scores(tex_content, problem_to_criteria):
    """
    Validate and fix scores in a .tex file before compilation.

    1. Checks that each rubric item has the correct point value from the rubric
    2. Removes items that don't belong to the subsection
    3. Recalculates subsection scores to match the sum of deductions

    Returns: (fixed_content, changes_made)
    """
    changes_made = []

    subsection_pattern = r'\\subsection\*\{(Problem\s+\d+\s+\([a-z]\))\s+Incorrect\s+Rubric\s+Items.*?Score:\s+\[([\d.]+)/([\d.]+)\]\}(.*?)(?=\\subsection|\\vspace\{0\.3em\}\s*\\hrule|\\vfill|\\end\{document\})'

    def fix_subsection(match):
        subsection_name = match.group(1)
        old_earned = float(match.group(2))
        max_score = float(match.group(3))
        content_block = match.group(4)
        full_match = match.group(0)

        if subsection_name not in problem_to_criteria:
            return full_match

        valid_criteria = problem_to_criteria[subsection_name]

        # Process items: fix point values and remove misplaced ones
        item_pattern = r'(% Only show items that lost points\s*\\item\s+\\textbf\{)([^}]+)(\}\s+\(\\textcolor\{late_red\}\{Missed\s+)([\d.]+)(\s+pts?\}\).*?)(?=% Only show items that lost points|\\end\{itemize\})'

        kept_items = []
        total_deducted = 0.0

        for item_match in re.finditer(item_pattern, content_block, re.DOTALL):
            prefix = item_match.group(1)
            criterion_tex = item_match.group(2)
            middle = item_match.group(3)
            old_points = float(item_match.group(4))
            suffix = item_match.group(5)

            criterion_clean = clean_criterion_name(criterion_tex)

            # Check if this criterion belongs to this subsection
            if criterion_clean in valid_criteria:
                correct_points = valid_criteria[criterion_clean]

                # Fix point value if wrong
                if abs(old_points - correct_points) > 0.01:
                    changes_made.append({
                        'type': 'point_value_fix',
                        'subsection': subsection_name,
                        'criterion': criterion_clean,
                        'old_points': old_points,
                        'new_points': correct_points
                    })

                    # Format new points
                    if correct_points == int(correct_points):
                        new_points_str = f"{int(correct_points)}.0"
                    else:
                        new_points_str = str(correct_points)

                    fixed_item = f"{prefix}{criterion_tex}{middle}{new_points_str}{suffix}"
                    kept_items.append(fixed_item)
                    total_deducted += correct_points
                else:
                    # Point value is correct, keep as-is
                    kept_items.append(item_match.group(0))
                    total_deducted += old_points
            else:
                # Item doesn't belong to this subsection - remove it
                changes_made.append({
                    'type': 'misplaced_item_removed',
                    'subsection': subsection_name,
                    'criterion': criterion_clean,
                    'points': old_points
                })

        # Recalculate the subsection score based on actual deductions
        new_earned = max_score - total_deducted

        # Check if score needs updating
        if abs(old_earned - new_earned) > 0.01:
            changes_made.append({
                'type': 'score_recalculation',
                'subsection': subsection_name,
                'old_earned': old_earned,
                'new_earned': new_earned,
                'total_deducted': total_deducted
            })

        # Rebuild the subsection
        # Find the itemize block
        itemize_start = re.search(r'\\begin\{itemize\}\[leftmargin=\*\]', content_block)
        itemize_end = re.search(r'\\end\{itemize\}', content_block)

        if itemize_start and itemize_end and kept_items:
            # Rebuild with kept items
            before_items = content_block[:itemize_start.end()]
            after_items = content_block[itemize_end.start():]

            new_content = before_items + '\n' + '\n'.join(kept_items) + '\n                            ' + after_items

            # Update the score in the header
            new_header = full_match[:match.end(1) - len(match.group(4))]
            new_header = new_header.replace(f'[{old_earned}/{max_score}]', f'[{new_earned}/{max_score}]')

            return new_header + new_content
        elif not kept_items:
            # No valid items - this shouldn't happen if the rubric score is correct,
            # but if it does, keep the subsection header but mark it
            changes_made.append({
                'type': 'warning',
                'subsection': subsection_name,
                'message': 'No valid items remaining after filtering'
            })

        return full_match

    fixed_content = re.sub(subsection_pattern, fix_subsection, tex_content, flags=re.DOTALL)

    return fixed_content, changes_made

def validate_tex_file(tex_path, rubric_path):
    """
    Standalone validation function for testing.
    Reads a .tex file, validates/fixes it, and returns the result.
    """
    problem_to_criteria = load_rubric_structure(rubric_path)

    with open(tex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    fixed_content, changes = validate_and_fix_tex_scores(content, problem_to_criteria)

    return fixed_content, changes

# For integration into reporter.py
def validate_and_fix_before_compilation(tex_content, rubric_path):
    """
    Main entry point for reporter.py to call before compiling PDFs.

    Args:
        tex_content: The LaTeX content as a string
        rubric_path: Path to the rubric YAML file

    Returns:
        fixed_content: The validated and fixed LaTeX content
        had_issues: Boolean indicating if any fixes were made
    """
    problem_to_criteria = load_rubric_structure(rubric_path)
    fixed_content, changes = validate_and_fix_tex_scores(tex_content, problem_to_criteria)

    had_issues = len(changes) > 0

    if had_issues:
        print(f"\n   🔍 SCORE VALIDATION DETECTED {len(changes)} ISSUE(S) - AUTO-FIXING:")

        # Group changes by type for clearer output
        point_fixes = [c for c in changes if c['type'] == 'point_value_fix']
        misplaced = [c for c in changes if c['type'] == 'misplaced_item_removed']
        recalcs = [c for c in changes if c['type'] == 'score_recalculation']
        warnings = [c for c in changes if c['type'] == 'warning']

        if point_fixes:
            print(f"\n   📝 Fixed {len(point_fixes)} incorrect point value(s):")
            for change in point_fixes:
                print(f"      • {change['subsection']}: {change['criterion'][:50]}")
                print(f"        Wrong: {change['old_points']} pts → Correct: {change['new_points']} pts")

        if misplaced:
            print(f"\n   🗑️  Removed {len(misplaced)} misplaced item(s) (from wrong problem section):")
            for change in misplaced:
                print(f"      • {change['subsection']}: {change['criterion'][:50]}")
                print(f"        ({change['points']} pts - item doesn't belong to this section)")

        if recalcs:
            print(f"\n   🔢 Recalculated {len(recalcs)} subsection score(s) to match deductions:")
            for change in recalcs:
                old_max = change['old_earned'] + change['total_deducted']
                new_max = change['new_earned'] + change['total_deducted']
                print(f"      • {change['subsection']}: {change['old_earned']:.1f}/{old_max:.1f} → {change['new_earned']:.1f}/{new_max:.1f}")
                print(f"        (Total deductions: {change['total_deducted']:.1f} pts)")

        if warnings:
            print(f"\n   ⚠️  {len(warnings)} warning(s):")
            for change in warnings:
                print(f"      • {change['subsection']}: {change['message']}")

        print(f"\n   ✓ All scoring issues fixed - proceeding with compilation\n")

    return fixed_content, had_issues

if __name__ == "__main__":
    # Test on a specific file
    import sys
    if len(sys.argv) > 1:
        tex_path = Path(sys.argv[1])
        rubric_path = Path("data/rubrics/ME373_HW3_rubric.yaml")

        fixed, changes = validate_tex_file(tex_path, rubric_path)

        print(f"Validated {tex_path.name}")
        print(f"Changes made: {len(changes)}")
        for change in changes:
            print(f"  {change}")
    else:
        print("Usage: python validate_and_fix_scores.py <tex_file>")
