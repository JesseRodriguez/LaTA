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

import yaml
import time
import argparse
from pathlib import Path
from src.lata.ingest import GradescopeIngestor
from src.lata.segmenter import LatexSegmenter
from src.lata.grader import LatexGrader, GradeResult, GradeItem
from src.lata.reporter import LatexReporter

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student", type=str, help="Target student name/ID")
    parser.add_argument(
        "--regrade",
        nargs='+',
        help="Force regrade for specific student(s) without versioning. Accepts multiple names/IDs separated by spaces."
    )
    args = parser.parse_args()

    # 1. Load Config & Assets
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)
    
    # Check for required PDF metadata file
    export_dir = Path(config['paths']['export_dir'])
    pdf_metadata_path = export_dir / 'pdf_submission_metadata.yml'

    if not pdf_metadata_path.exists():
        print(f"❌ ERROR: PDF submission metadata not found!")
        print(f"   Expected: {pdf_metadata_path}")
        print(f"   This file is required for accurate timestamp tracking and versioning.")
        print(f"   Please ensure you have downloaded the complete Gradescope export.")
        return

    rubric_path = Path(config['paths']['rubrics_dir']) / config['paths']['rubric_file']
    with open(rubric_path, 'r') as f:
        full_rubric = yaml.safe_load(f)

    # 2. Detect corrections mode
    assignment_name = config['grading']['assignment_name']
    base_assignment = assignment_name.replace('_CORRECTIONS', '')
    is_corrections_mode = assignment_name.endswith('_CORRECTIONS')

    if is_corrections_mode:
        print(f"\n🔄 CORRECTIONS MODE detected")
        print(f"   Base assignment: {base_assignment}")
        print(f"   Original metadata: data/output/{base_assignment}_Feedback/")
        print()

        # Verify original metadata exists
        original_path = Path(f"data/output/{base_assignment}_Feedback/grading_metadata.yaml")
        if not original_path.exists():
            print(f"❌ ERROR: Original metadata not found at {original_path}")
            print(f"   Cannot proceed with corrections mode.")
            print(f"   Please ensure the original assignment was graded first.")
            return

        # Filter out extra credit items from rubric in corrections mode
        print("   📝 Filtering extra credit items from rubric (corrections mode)")
        filtered_rubric = {}
        total_ec_items_removed = 0

        for problem_label, sub_problems in full_rubric.items():
            filtered_sub_problems = []

            for section in sub_problems:
                filtered_items = []

                for item in section.get('items', []):
                    if not item.get('is_extra_credit', False):
                        filtered_items.append(item)
                    else:
                        total_ec_items_removed += 1

                # Only keep section if it has non-EC items
                if filtered_items:
                    filtered_section = section.copy()
                    filtered_section['items'] = filtered_items
                    filtered_sub_problems.append(filtered_section)

            filtered_rubric[problem_label] = filtered_sub_problems

        full_rubric = filtered_rubric
        print(f"   ✓ Removed {total_ec_items_removed} extra credit items from grading rubric")
        print()

    # 3. Initialize Workers
    output_dir = f"data/output/{assignment_name}_Feedback"
    segmenter = LatexSegmenter()
    grader = LatexGrader(output_dir=output_dir)
    reporter = LatexReporter(
        output_dir=output_dir,
        is_corrections_mode=is_corrections_mode,
        base_assignment=base_assignment if is_corrections_mode else None
    )

    # 4. Process Reference Solution (Segment it!)
    print("🔹 Segmenting Reference Solution...")
    with open(config['paths']['solution_file'], 'r') as f:
        sol_content = f.read()
    
    sol_chunks_map = {}
    sol_seg_result = segmenter.segment_submission(sol_content)
    for chunk in sol_seg_result.chunks:
        sol_chunks_map[chunk.label] = chunk.content
        print(f"   - Found {chunk.label}")

    # 5. Load Students
    ingestor = GradescopeIngestor(config['paths']['export_dir'], anonymize=config['grading']['anonymize'])
    ingest_result = ingestor.load()

    targets = ingest_result.submissions
    regrade_list = []

    # Handle --regrade flag
    if args.regrade:
        print(f"🔄 REGRADE MODE: Force regrading {len(args.regrade)} student(s)")
        print("   (Skipping version checks, will overwrite existing reports)\n")

        # Build list of students to regrade
        for query in args.regrade:
            matches = [s for s in ingest_result.submissions
                      if query.lower() in s.student_name.lower() or query in s.anonymized_id]
            if matches:
                for match in matches:
                    if match not in regrade_list:
                        regrade_list.append(match)
                        print(f"   ✓ Will regrade: {match.student_name}")
            else:
                print(f"   ⚠️  No student found matching '{query}'")

        if not regrade_list:
            print("\n❌ No students found to regrade")
            return

        targets = regrade_list
        print()

    # Handle --student flag (existing single-student filter)
    elif args.student:
        targets = [s for s in targets if args.student.lower() in s.student_name.lower() or args.student in s.anonymized_id]
        if not targets:
            print(f"❌ No student found for '{args.student}'")
            return

    # 6. Grading Loop
    for student in targets:
        print(f"\n🎓 Processing: {student.student_name} ({student.anonymized_id})")

        # Check if we should skip this student (already graded with same timestamp)
        # Skip the skip check if this student is being force-regraded
        is_regrade = args.regrade and student in regrade_list
        skip_previously_graded = config.get('grading', {}).get('skip_previously_graded', True)

        if not is_regrade and skip_previously_graded and reporter.should_skip_student(student.student_name, student.created_at):
            print(f"   ⏭️  Skipping (already graded, no new submission)")
            continue

        if is_regrade:
            print(f"   🔄 REGRADE: Forcing fresh grading (no versioning)")

        # CORRECTIONS MODE CHECK: Verify student had an original submission
        if is_corrections_mode:
            safe_name = student.student_name.replace(" ", "_").replace("(", "").replace(")", "")
            if safe_name not in reporter.original_metadata:
                print(f"   ⚠️  No original submission found - generating placeholder report")
                pdf_path = reporter.generate_no_original_submission_report(
                    student_name=student.student_name,
                    student_id=student.anonymized_id,
                    submission_created_at=student.created_at
                )
                if pdf_path:
                    print(f"   ✅ Placeholder report generated: {pdf_path}")
                else:
                    print(f"   ❌ Failed to generate placeholder report")
                continue

        # A. Segment Student Work
        stu_seg = segmenter.segment_submission(student.cleaned_content)
        stu_chunks_map = {c.label: c.content for c in stu_seg.chunks}

        all_grade_items = []

        # B. Loop over Rubric Problems (Problem 1, Problem 2...)
        for problem_label, sub_problems in full_rubric.items():

            # Get the relevant text chunks
            # Default to empty string if missing (student left it blank)
            stu_text = stu_chunks_map.get(problem_label, "(Student left this problem blank.)")
            sol_text = sol_chunks_map.get(problem_label, "(No reference solution found for this section.)")

            # Grade this chunk
            items = grader.grade_chunk(
                problem_label=problem_label,
                student_text=stu_text,
                solution_text=sol_text,
                rubric_items=sub_problems,
                student_id=student.anonymized_id
            )

            all_grade_items.extend(items)

        # C. Aggregate & Report
        if not all_grade_items:
            print("   ⚠️ No grade items produced. Skipping report.")
            continue

        # Separate regular and extra credit items
        regular_items = [i for i in all_grade_items if not getattr(i, 'is_extra_credit', False)]
        extra_credit_items = [i for i in all_grade_items if getattr(i, 'is_extra_credit', False)]

        # Calculate regular score and max
        regular_score = sum(i.score for i in regular_items)
        regular_max = sum(i.max_score for i in regular_items)

        # Calculate extra credit earned
        extra_credit_earned = sum(i.score for i in extra_credit_items)

        # Final totals: extra credit adds to numerator only
        total_score = regular_score + extra_credit_earned
        max_total = regular_max  # Extra credit max NOT included

        # Create Final Result Object
        final_result = GradeResult(
            student_id=student.anonymized_id,
            student_name=student.student_name,
            items=all_grade_items,
            total_score=total_score,
            general_comments=f"Total Score: {total_score}/{max_total}. See detailed breakdown below."
        )

        pdf_path = reporter.generate_report(
            final_result,
            submission_created_at=student.created_at,
            force_regrade=is_regrade,
            segmentation_warning=stu_seg.segmentation_warning,
            extension_hours=student.extension_hours
        )
        if pdf_path:
            print(f"   ✅ Report generated: {pdf_path}")
        else:
            print("   ❌ Report generation failed.")

    # 6. Save failed submissions if any
    grader.save_failed_submissions_to_file()

if __name__ == "__main__":
    main()