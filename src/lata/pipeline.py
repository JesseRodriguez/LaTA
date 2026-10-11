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
Grade one assignment: segment, grade every problem, write the reports.

This is the body of grade.py, callable on its own so the watcher (lata.watch) can run it
over and over on whichever students are ready, with timestamps it has decided itself.
grade_assignment() returns what happened to each student, which the watcher uses to
decide whether a report can be emailed without review.
"""

import sys
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from lata.ingest import GradescopeIngestor, StudentSubmission
from lata.segmenter import LatexSegmenter
from lata.grader import LatexGrader, GradeResult
from lata.reporter import LatexReporter
from lata.scheduler import ProblemJob, StudentPlan, ProgressMonitor, GradingCancelled, run_jobs
from lata.serving import prepare_grader


def safe_name(student_name: str) -> str:
    """Filename form of a student's name, as used for Feedback_<name>.pdf everywhere."""
    return student_name.replace(" ", "_").replace("(", "").replace(")", "")


@dataclass
class GradingOptions:
    concurrency: int = 1
    watch_slot: int = 1
    dashboard: bool = True           # live dashboard when concurrency > 1 and stdout is a tty
    regrade: Optional[List[str]] = None
    student: Optional[str] = None


@dataclass
class StudentOutcome:
    name: str
    sid: str
    email: str
    display_id: str
    created_at: str
    report: Optional[Path] = None
    failed_problems: List[str] = field(default_factory=list)
    segmentation_warning: bool = False
    security_flags: List[str] = field(default_factory=list)
    validation_fixes: int = 0
    placeholder: bool = False        # corrections mode, no original submission

    def hold_reasons(self) -> List[str]:
        """Why this report should not be emailed without someone looking at it first."""
        reasons = []
        if self.report is None:
            reasons.append("no report was generated")
        if self.failed_problems:
            reasons.append(f"grading failed for {', '.join(self.failed_problems)}")
        if self.segmentation_warning:
            reasons.append("problems could not be located in the submission (segmentation warning)")
        if self.security_flags:
            reasons.append(f"possible prompt injection in {', '.join(self.security_flags)}")
        return reasons


@dataclass
class RunResult:
    outcomes: Dict[str, StudentOutcome] = field(default_factory=dict)   # by student name
    interrupted: bool = False
    error: Optional[str] = None
    skipped: int = 0


def load_rubric(config: dict, is_corrections_mode: bool) -> dict:
    rubric_path = Path(config['paths']['rubrics_dir']) / config['paths']['rubric_file']
    with open(rubric_path, 'r') as f:
        full_rubric = yaml.safe_load(f)
    if not is_corrections_mode:
        return full_rubric

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

    print(f"   ✓ Removed {total_ec_items_removed} extra credit items from grading rubric")
    print()
    return filtered_rubric


def grade_assignment(config: dict, config_path: str, options: GradingOptions,
                     select: Optional[Callable[[StudentSubmission], bool]] = None,
                     created_at: Optional[Callable[[StudentSubmission], Optional[str]]] = None) -> RunResult:
    """Grade the submissions in config['paths']['export_dir'] and write their reports.

    select:     if given, only students it returns True for are considered.
    created_at: returns the submission time to use for a student (or None to keep the
                later-of-.tex-and-.pdf time the ingestor works out from the export). The
                watcher passes the times it has frozen, since a later export may show a
                newer (corrections) PDF.
    """
    result = RunResult()

    # Detect corrections mode
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
            result.error = f"original metadata not found at {original_path}"
            return result

    full_rubric = load_rubric(config, is_corrections_mode)

    # Initialize Workers
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

    # Load Students
    ingestor = GradescopeIngestor(config['paths']['export_dir'], anonymize=config['grading']['anonymize'])
    ingest_result = ingestor.load()

    targets = ingest_result.submissions
    if select is not None:
        targets = [s for s in targets if select(s)]
    if created_at is not None:
        targets = [s.model_copy(update={'created_at': t}) if (t := created_at(s)) else s
                   for s in targets]
    regrade_list = []

    # Handle --regrade flag
    if options.regrade:
        print(f"🔄 REGRADE MODE: Force regrading {len(options.regrade)} student(s)")
        print("   (Skipping version checks, will overwrite existing reports)\n")

        # Build list of students to regrade
        for query in options.regrade:
            matches = [s for s in targets
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
            return result

        targets = regrade_list
        print()

    # Handle --student flag (existing single-student filter)
    elif options.student:
        targets = [s for s in targets if options.student.lower() in s.student_name.lower() or options.student in s.anonymized_id]
        if not targets:
            print(f"❌ No student found for '{options.student}'")
            return result

    if not targets:
        print("\nNothing to grade.")
        return result

    # Process Reference Solution (Segment it!)
    print("🔹 Segmenting Reference Solution...")
    with open(config['paths']['solution_file'], 'r') as f:
        sol_content = f.read()

    sol_chunks_map = {}
    sol_seg_result = segmenter.segment_submission(sol_content)
    for chunk in sol_seg_result.chunks:
        sol_chunks_map[chunk.label] = chunk.content
        print(f"   - Found {chunk.label}")

    # Plan: decide who needs grading and segment their work, before any grading.
    # Segmentation is nearly always regex; its LLM fallback uses a different model than the
    # grader, so doing it all up front means the server swaps models once instead of
    # mid-run while grading slots are busy.
    skip_previously_graded = config.get('grading', {}).get('skip_previously_graded', True)
    plans = []

    for student in targets:
        # Check if we should skip this student (already graded with same timestamp)
        # Skip the skip check if this student is being force-regraded
        is_regrade = bool(options.regrade) and student in regrade_list

        if not is_regrade and skip_previously_graded and reporter.should_skip_student(student.student_name, student.created_at):
            result.skipped += 1
            continue

        print(f"\n🎓 Preparing: {student.student_name} ({student.anonymized_id})")
        if is_regrade:
            print(f"   🔄 REGRADE: Forcing fresh grading (no versioning)")

        outcome = StudentOutcome(name=student.student_name, sid=student.student_sid,
                                 email=student.student_email, display_id=student.anonymized_id,
                                 created_at=student.created_at)

        # CORRECTIONS MODE CHECK: Verify student had an original submission
        if is_corrections_mode:
            if safe_name(student.student_name) not in reporter.original_metadata:
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
                outcome.placeholder = True
                outcome.report = Path(pdf_path) if pdf_path else None
                result.outcomes[student.student_name] = outcome
                continue

        # A. Segment Student Work
        stu_seg = segmenter.segment_submission(student.cleaned_content)
        stu_chunks_map = {c.label: c.content for c in stu_seg.chunks}

        plan = StudentPlan(student=student, is_regrade=is_regrade,
                           segmentation_warning=stu_seg.segmentation_warning)
        outcome.segmentation_warning = bool(stu_seg.segmentation_warning)
        result.outcomes[student.student_name] = outcome

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

    if result.skipped:
        print(f"\n⏭️  Skipped {result.skipped} student(s) already graded with no new submission")

    total_problems = sum(len(p.jobs) for p in plans)
    if not plans:
        # Nothing to do: never load a model just to find that out
        print("\nNothing to grade.")
        return result

    # Resolve the grading model now that there is work, and serve it if it is a local GGUF
    try:
        server = prepare_grader(config, options.concurrency, log_dir=Path(output_dir))
    except RuntimeError as e:
        print(f"\n❌ {e}")
        result.error = str(e)
        return result
    try:
        grader = LatexGrader(config_path=config_path, output_dir=output_dir,
                             concurrency=options.concurrency, config=config)
        _grade_and_report(options, output_dir, plans, total_problems,
                          assignment_name, grader, reporter, result)
    finally:
        if server is not None:
            server.stop()
    return result


def _grade_and_report(options, output_dir, plans, total_problems,
                      assignment_name, grader, reporter, result):
    concurrency = options.concurrency
    print(f"\n📋 {len(plans)} student(s), {total_problems} problem(s) to grade "
          f"with {concurrency} slot{'s' if concurrency != 1 else ''}")
    if concurrency > 1:
        print(f"   The grading server must allow at least {concurrency} parallel requests "
              f"(Ollama: OLLAMA_NUM_PARALLEL >= {concurrency}), or the extra requests just queue.")

    # Grade
    monitor = None
    if concurrency > 1:
        use_dashboard = options.dashboard and sys.stdout.isatty()
        monitor = ProgressMonitor(
            total_students=len(plans), total_problems=total_problems, concurrency=concurrency,
            title=f"{assignment_name} · {grader.model}",
            log_path=Path(output_dir) / "grading_run.log",
            watch_slot=options.watch_slot, dashboard=use_dashboard,
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
            if not ok and not cancelled:
                result.outcomes[student.student_name].failed_problems.append(job.problem_label)
            if monitor:
                monitor.end(ok, cached=getattr(grader._tls, "last_from_cache", False), cancelled=cancelled)

    def finish_student(plan, all_grade_items):
        # C. Aggregate & Report (always on the main thread: reports, pdflatex and
        # grading_metadata.yaml are never touched concurrently)
        student = plan.student
        tag = f"[{student.student_name}] " if concurrency > 1 else ""
        outcome = result.outcomes[student.student_name]
        _collect_debug_flags(grader, student, plan, outcome)

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
            outcome.report = Path(pdf_path)
        else:
            print(f"   ❌ {tag}Report generation failed.")
        if monitor:
            monitor.student_done(bool(pdf_path))

    if monitor:
        monitor.start()
    try:
        run_jobs(plans, grade_job, finish_student, concurrency, monitor, grader.stop_event)
    except KeyboardInterrupt:
        result.interrupted = True
        grader.stop_event.set()
    finally:
        if monitor:
            monitor.stop()

    if result.interrupted:
        print("\n⏹️  Stopped. Students whose reports were generated are complete; problems graded "
              "for unfinished students were saved and will be reused when you run grade.py again.")

    # Save failed submissions if any
    grader.save_failed_submissions_to_file()


def _collect_debug_flags(grader, student, plan, outcome):
    """Security flags and score-validation fixes the grader left in this student's debug dirs."""
    for job in plan.jobs:
        debug_dir = grader.debug_dir / student.anonymized_id / job.problem_label.replace(" ", "_")
        if (debug_dir / "SECURITY_FLAG.txt").exists():
            outcome.security_flags.append(job.problem_label)
        validation = debug_dir / "score_validation.txt"
        if validation.exists():
            try:
                outcome.validation_fixes += sum(
                    1 for line in validation.read_text().splitlines() if line.startswith("[fix]"))
            except OSError:
                pass
