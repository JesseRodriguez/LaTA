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
One-time script to retroactively add late penalties to already-graded submissions.

This script:
1. Loads the Gradescope submission metadata
2. Identifies late submissions based on config due_date
3. Finds existing .tex files in the logs directory
4. Adds late penalty warning box to the .tex files
5. Recompiles PDFs with late penalty notices
6. Reports which submissions were updated

Usage:
    python retroactive_late_penalties.py
"""

import yaml
import subprocess
from pathlib import Path
from datetime import datetime
from src.lata.ingest import GradescopeIngestor
from src.lata.reporter import LatexReporter


def main():
    print("=" * 80)
    print("RETROACTIVE LATE PENALTY APPLICATION")
    print("=" * 80)
    print()

    # 1. Load configuration
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)

    late_conf = config.get('grading', {}).get('late_penalties', {})
    if not late_conf.get('enabled', False):
        print("❌ Late penalties are disabled in config.yaml")
        print("   Please set grading.late_penalties.enabled: true")
        return

    due_date_str = late_conf.get('due_date')
    if not due_date_str:
        print("❌ No due date configured in config.yaml")
        print("   Please set grading.late_penalties.due_date")
        return

    penalty_per_day = late_conf.get('penalty_per_day', 0.20)

    print(f"📅 Due Date (GMT):  {due_date_str}")
    print(f"📉 Penalty Rate:    {penalty_per_day * 100}% per day")
    print()

    # 2. Load submissions from Gradescope metadata
    ingestor = GradescopeIngestor(
        config['paths']['export_dir'],
        anonymize=config['grading']['anonymize']
    )
    ingest_result = ingestor.load()

    # 3. Find output directory
    output_dir = Path(f"data/output/{config['grading']['assignment_name']}_Feedback")
    logs_dir = output_dir / "logs"

    if not output_dir.exists():
        print(f"❌ Output directory not found: {output_dir}")
        return

    if not logs_dir.exists():
        print(f"❌ Logs directory not found: {logs_dir}")
        return

    print(f"📂 Output Directory: {output_dir}")
    print(f"📂 Logs Directory:   {logs_dir}")
    print()

    # 4. Initialize reporter for late penalty calculation
    reporter = LatexReporter(output_dir=str(output_dir))

    # 5. Process each submission
    late_submissions = []
    ontime_submissions = []

    for student in ingest_result.submissions:
        # Calculate if submission is late (use dummy scores for initial check)
        penalty_info = reporter.calculate_late_penalty(student.created_at, 100.0, 100.0, student.extension_hours)  # Use 100 as dummy scores

        if penalty_info['is_late']:
            late_submissions.append({
                'student': student,
                'penalty_info': penalty_info
            })
        else:
            ontime_submissions.append(student)

    print(f"✅ On-time submissions: {len(ontime_submissions)}")
    print(f"⏰ Late submissions:    {len(late_submissions)}")
    print()

    if not late_submissions:
        print("🎉 No late submissions found! Nothing to update.")
        return

    # 6. Report late submissions
    print("=" * 80)
    print("LATE SUBMISSIONS DETECTED")
    print("=" * 80)
    print()

    for item in late_submissions:
        student = item['student']
        penalty = item['penalty_info']

        print(f"📛 {student.student_name}")
        print(f"   Submitted: {student.created_at}")
        print(f"   Days Late: {penalty['days_late']} day(s)")
        print(f"   Penalty:   {penalty['penalty_percentage'] * 100}%")
        print()

    # 7. Ask for confirmation
    response = input("Do you want to recompile PDFs with late penalties? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("❌ Aborted. No changes made.")
        return

    print()
    print("=" * 80)
    print("RECOMPILING PDFs WITH LATE PENALTIES")
    print("=" * 80)
    print()

    # 8. Process each late submission
    updated_count = 0
    failed_count = 0

    for item in late_submissions:
        student = item['student']
        penalty = item['penalty_info']

        # Generate safe filename
        if student.student_name:
            safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")
        else:
            safe_name = student.anonymized_id.replace(" ", "_").replace("(", "").replace(")", "")

        # Look for existing .tex file in logs
        tex_file = logs_dir / f"Feedback_{safe_name}.tex"
        pdf_file = output_dir / f"Feedback_{safe_name}.pdf"

        if not tex_file.exists():
            print(f"⚠️  {student.student_name}: .tex file not found at {tex_file}")
            failed_count += 1
            continue

        # Read existing .tex content
        with open(tex_file, 'r') as f:
            tex_content = f.read()

        # Check if late penalty box already exists
        if "LATE SUBMISSION PENALTY" in tex_content:
            print(f"⏭️  {student.student_name}: Late penalty already present, skipping")
            continue

        # Add color definitions if not present
        if "late_red" not in tex_content:
            # Find the color definitions section (after other \definecolor commands)
            color_insert_point = tex_content.find("\\definecolor{hint_border}")
            if color_insert_point != -1:
                # Find the end of that line
                line_end = tex_content.find('\n', color_insert_point)
                tex_content = (
                    tex_content[:line_end+1] +
                    "\\definecolor{late_red}{RGB}{220, 53, 69}\n" +
                    "\\definecolor{late_bg}{RGB}{255, 243, 245}\n" +
                    tex_content[line_end+1:]
                )
            else:
                # Fallback: add after \geometry line
                geom_point = tex_content.find("\\geometry{")
                if geom_point != -1:
                    line_end = tex_content.find('\n', geom_point)
                    tex_content = (
                        tex_content[:line_end+1] +
                        "\n% Late penalty colors\n" +
                        "\\definecolor{late_red}{RGB}{220, 53, 69}\n" +
                        "\\definecolor{late_bg}{RGB}{255, 243, 245}\n\n" +
                        tex_content[line_end+1:]
                    )

        # Extract total_score and max_score from .tex file
        import re

        # Get earned score from TOTAL row
        total_match = re.search(r'\\textbf\{TOTAL\}.*?\\textbf\{([0-9.]+)\}', tex_content)
        if not total_match:
            print(f"⚠️  {student.student_name}: Could not find total score in .tex file")
            failed_count += 1
            continue
        total_score = float(total_match.group(1))

        # Calculate max_score by summing all problem max scores from the table
        # Pattern: Problem/Section & score & max
        max_scores = re.findall(r'\\hline\n[^&]+&[^&]+&\s*([0-9.]+)\s*\\\\', tex_content)
        # Filter out the TOTAL row (which has '-' instead of a number)
        max_score = sum(float(m) for m in max_scores if m)

        if max_score == 0:
            print(f"⚠️  {student.student_name}: Could not calculate max score from .tex file")
            failed_count += 1
            continue

        # Recalculate penalty with actual scores
        penalty = reporter.calculate_late_penalty(student.created_at, total_score, max_score, student.extension_hours)

        # Build late penalty LaTeX block
        late_penalty_block = f"""
% --- LATE PENALTY WARNING (retroactively added) ---
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
        \\textbf{{Days Late:}} & {penalty['days_late']} day(s) \\\\
        \\textbf{{Penalty Rate:}} & {penalty['penalty_percentage'] * 100}\\% ({penalty['penalty_percentage'] * 100 / penalty['days_late']}\\% per day) \\\\
        \\textbf{{Points Before Penalty:}} & {total_score} \\\\
        \\textbf{{Points Deducted:}} & -{penalty['points_deducted']} \\\\
        \\hline
        \\textbf{{Final Score:}} & \\textbf{{{penalty['final_score']}}} \\\\
        \\end{{tabular}}

        \\vspace{{0.3em}}
        \\small{{\\textit{{Note: The grading feedback below reflects your earned score before the late penalty.}}}}
        \\vspace{{0.3em}}
    }}
}}
\\end{{center}}

\\vspace{{1em}}
"""

        # Insert late penalty block after \maketitle
        if "\\maketitle" in tex_content:
            tex_content = tex_content.replace("\\maketitle", f"\\maketitle{late_penalty_block}")
        else:
            print(f"⚠️  {student.student_name}: Could not find \\maketitle in .tex file")
            failed_count += 1
            continue

        # Write updated .tex file back to logs
        updated_tex_file = logs_dir / f"Feedback_{safe_name}_with_penalty.tex"
        with open(updated_tex_file, 'w') as f:
            f.write(tex_content)

        # Compile PDF
        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", str(output_dir), str(updated_tex_file)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60
            )

            # Move the generated PDF to the correct name
            temp_pdf = output_dir / f"Feedback_{safe_name}_with_penalty.pdf"
            if temp_pdf.exists():
                temp_pdf.replace(pdf_file)
                print(f"✅ {student.student_name}: PDF updated ({penalty['days_late']} days late, -{penalty['penalty_percentage']*100}%)")
                updated_count += 1
            else:
                print(f"❌ {student.student_name}: PDF compilation failed")
                failed_count += 1

            # Clean up auxiliary files
            for ext in ['.aux', '.log', '.out']:
                aux_file = output_dir / f"Feedback_{safe_name}_with_penalty{ext}"
                if aux_file.exists():
                    aux_file.unlink()

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"❌ {student.student_name}: PDF compilation failed: {e}")
            failed_count += 1

    # 9. Summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"✅ Successfully updated: {updated_count} PDFs")
    print(f"❌ Failed:              {failed_count} PDFs")
    print(f"📊 Total late:          {len(late_submissions)} submissions")
    print()
    print("🎉 Done! Please review the updated PDFs in:")
    print(f"   {output_dir}")


if __name__ == "__main__":
    main()
