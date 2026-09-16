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

import sys
import yaml
import time
import argparse
from pathlib import Path
# Import as `lata`, the same package name the modules use for each other. Importing as
# `src.lata` loaded a second copy of every module, so classes such as GradeItem existed
# twice -- harmless for duck-typed data, but it breaks exception matching.
from lata.ingest import GradescopeIngestor
from lata.segmenter import LatexSegmenter
from lata.grader import LatexGrader, GradeResult, GradeItem
from lata.reporter import LatexReporter
from lata.scheduler import ProblemJob, StudentPlan, ProgressMonitor, GradingCancelled, run_jobs
from lata.serving import prepare_grader
from lata.inbox import prepare_from_inbox

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student", type=str, help="Target student name/ID")
    parser.add_argument(
        "--regrade",
        nargs='+',
        help="Force regrade for specific student(s) without versioning. Accepts multiple names/IDs separated by spaces."
    )
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to the configuration file (default: config.yaml)")
    parser.add_argument("--inbox", type=str, default=None,
                        help="Folder holding Gradescope export .zip files to unpack before "
                             "grading (overrides paths.inbox; default data/input/inbox)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Grading requests to run in parallel (overrides grading.concurrency). "
                             "The LLM server must allow at least this many: for Ollama set "
                             "OLLAMA_NUM_PARALLEL >= N and restart it.")
    parser.add_argument("--watch-slot", type=int, default=1,
                        help="With concurrency > 1, which slot's thinking to show live on the dashboard (default: 1)")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="With concurrency > 1, print plain log lines and a periodic status line "
                             "instead of the live dashboard (automatic when output is not a terminal)")
    args = parser.parse_args()

    # 1. Load Config & Assets
    config_path = args.config
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    concurrency = args.concurrency if args.concurrency is not None else config.get('grading', {}).get('concurrency', 1)
    concurrency = max(1, int(concurrency))

    # Unpack anything waiting in the inbox: the Gradescope .zip exports go straight in,
    # no unzipping or renaming by hand
    export_dir = Path(config['paths']['export_dir'])
    inbox_dir = args.inbox or config.get('paths', {}).get('inbox', 'data/input/inbox')
    prepare_from_inbox(inbox_dir, export_dir)

    # Check for required PDF metadata file
    pdf_metadata_path = export_dir / 'pdf_submission_metadata.yml'

    if not pdf_metadata_path.exists():
        print(f"❌ ERROR: PDF submission metadata not found!")
        print(f"   Expected: {pdf_metadata_path}")
        print(f"   This file is required for accurate timestamp tracking and versioning.")
        print(f"   Late penalties use the later of the .tex and .pdf submission times.")
        print(f"   Drop the .pdf assignment's Gradescope export into {inbox_dir}/, or place")
        print(f"   its submission_metadata.yml at the path above.")
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
    # The grading model is resolved and served later, once we know there is work for it.
    output_dir = f"data/output/{assignment_name}_Feedback"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    segmenter = LatexSegmenter(config_path=config_path, config=config)
    reporter = LatexReporter(
        output_dir=output_dir,
        config_path=config_path,
        config=config,
        is_corrections_mode=is_corrections_mode,
        base_assignment=base_assignment if is_corrections_mode else None
    )
    _run(args, config, config_path, concurrency, output_dir, full_rubric, assignment_name,
         is_corrections_mode, segmenter, reporter)


def _run(args, config, config_path, concurrency, output_dir, full_rubric, assignment_name,
         is_corrections_mode, segmenter, reporter):

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

    # 6. Plan: decide who needs grading and segment their work, before any grading.
    # Segmentation is nearly always regex; its LLM fallback uses a different model than the
    # grader, so doing it all up front means the server swaps models once instead of
    # mid-run while grading slots are busy.
    skip_previously_graded = config.get('grading', {}).get('skip_previously_graded', True)
    plans = []
    skipped = 0

    for student in targets:
        # Check if we should skip this student (already graded with same timestamp)
        # Skip the skip check if this student is being force-regraded
        is_regrade = bool(args.regrade) and student in regrade_list

        if not is_regrade and skip_previously_graded and reporter.should_skip_student(student.student_name, student.created_at):
            skipped += 1
            continue

        print(f"\n🎓 Preparing: {student.student_name} ({student.anonymized_id})")
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

        plan = StudentPlan(student=student, is_regrade=is_regrade,
                           segmentation_warning=stu_seg.segmentation_warning)

        # B. One job per rubric problem (Problem 1, Problem 2...)
        for problem_index, (problem_label, sub_problems) in enumerate(full_rubric.items()):
            # Default to empty string if missing (student left it blank)
            plan.jobs.append(ProblemJob(
                student_index=len(plans),
                problem_index=problem_index,
                problem_label=problem_label,
                student_text=stu_chunks_map.get(problem_label, "(Student left this problem blank.)"),
                solution_text=sol_chunks_map.get(problem_label, "(No reference solution found for this section.)"),
                rubric_items=sub_problems,
            ))
        plans.append(plan)

    if skipped:
        print(f"\n⏭️  Skipped {skipped} student(s) already graded with no new submission")

    total_problems = sum(len(p.jobs) for p in plans)
    if not plans:
        # Nothing to do: never load a model just to find that out
        print("\nNothing to grade.")
        return

    # Resolve the grading model now that there is work, and serve it if it is a local GGUF
    try:
        server = prepare_grader(config, concurrency, log_dir=Path(output_dir))
    except RuntimeError as e:
        print(f"\n❌ {e}")
        return
    try:
        grader = LatexGrader(config_path=config_path, output_dir=output_dir,
                             concurrency=concurrency, config=config)
        _grade_and_report(args, config, concurrency, output_dir, plans, total_problems,
                          assignment_name, grader, reporter)
    finally:
        if server is not None:
            server.stop()


def _grade_and_report(args, config, concurrency, output_dir, plans, total_problems,
                      assignment_name, grader, reporter):
    print(f"\n📋 {len(plans)} student(s), {total_problems} problem(s) to grade "
          f"with {concurrency} slot{'s' if concurrency != 1 else ''}")
    if concurrency > 1:
        print(f"   The grading server must allow at least {concurrency} parallel requests "
              f"(Ollama: OLLAMA_NUM_PARALLEL >= {concurrency}), or the extra requests just queue.")

    # 7. Grade
    monitor = None
    if concurrency > 1:
        use_dashboard = not args.no_dashboard and sys.stdout.isatty()
        monitor = ProgressMonitor(
            total_students=len(plans), total_problems=total_problems, concurrency=concurrency,
            title=f"{assignment_name} · {grader.model}",
            log_path=Path(output_dir) / "grading_run.log",
            watch_slot=args.watch_slot, dashboard=use_dashboard,
        )
        grader.monitor = monitor

    def grade_job(plan, job):
        student = plan.student
        if concurrency > 1:
            grader.set_context(student.student_name)
        elif job.problem_index == 0:
            # Single slot keeps the original per-student console flow
            print(f"\n🎓 Processing: {student.student_name} ({student.anonymized_id})")
        if monitor:
            monitor.begin(student.student_name, job.problem_label)
        items, ok, cancelled = [], False, False
        try:
            items = grader.grade_chunk(
                problem_label=job.problem_label,
                student_text=job.student_text,
                solution_text=job.solution_text,
                rubric_items=job.rubric_items,
                student_id=student.anonymized_id,
                use_cache=not plan.is_regrade,
            )
            ok = bool(items)
            return items
        except GradingCancelled:
            cancelled = True
            raise
        except Exception as e:
            # grade_chunk handles its own errors; this guards the scheduler against anything else
            print(f"   ❌ [{student.student_name}] Unexpected error grading {job.problem_label}: {e}")
            return []
        finally:
            if monitor:
                monitor.end(ok, cached=getattr(grader._tls, "last_from_cache", False), cancelled=cancelled)

    def finish_student(plan, all_grade_items):
        # C. Aggregate & Report (always on the main thread: reports, pdflatex and
        # grading_metadata.yaml are never touched concurrently)
        student = plan.student
        tag = f"[{student.student_name}] " if concurrency > 1 else ""

        if not all_grade_items:
            print(f"   ⚠️ {tag}No grade items produced. Skipping report.")
            if monitor:
                monitor.student_done(False)
            return

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
            force_regrade=plan.is_regrade,
            segmentation_warning=plan.segmentation_warning,
            extension_hours=student.extension_hours
        )
        if pdf_path:
            print(f"   ✅ {tag}Report generated: {pdf_path}")
        else:
            print(f"   ❌ {tag}Report generation failed.")
        if monitor:
            monitor.student_done(bool(pdf_path))

    interrupted = False
    if monitor:
        monitor.start()
    try:
        run_jobs(plans, grade_job, finish_student, concurrency, monitor, grader.stop_event)
    except KeyboardInterrupt:
        interrupted = True
        grader.stop_event.set()
    finally:
        if monitor:
            monitor.stop()

    if interrupted:
        print("\n⏹️  Stopped. Students whose reports were generated are complete; problems graded "
              "for unfinished students were saved and will be reused when you run grade.py again.")

    # 8. Save failed submissions if any
    grader.save_failed_submissions_to_file()

if __name__ == "__main__":
    main()
