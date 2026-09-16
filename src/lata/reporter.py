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

import os
import re
import shutil
import subprocess
import yaml
from pathlib import Path
import jinja2
from openai import OpenAI

# Import validation module

class LatexReporter:
    # LaTeX auxiliary file extensions to clean up
    LATEX_AUX_EXTENSIONS = ['.aux', '.log', '.out', '.toc', '.lof', '.lot', '.fls', '.fdb_latexmk', '.synctex.gz']

    def __init__(self, template_dir="templates", output_dir="data/output", config_path="config.yaml", is_corrections_mode=False, base_assignment=None, config: dict = None):
        self.template_dir = Path(template_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create logs subdirectory for auxiliary files
        self.logs_dir = self.output_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Grading metadata for tracking timestamps and versions
        self.metadata_path = self.output_dir / "grading_metadata.yaml"
        self.grading_metadata = self._load_grading_metadata()

        # Corrections mode parameters
        self.is_corrections_mode = is_corrections_mode
        self.base_assignment = base_assignment
        self.original_metadata = {}

        # Load original metadata if corrections mode
        if self.is_corrections_mode and self.base_assignment:
            self._load_original_metadata()

        # Load configuration
        self.config_path = Path(config_path)
        if config is not None:
            self.config = config
        else:
            with open(config_path, 'r') as f:
                self.config = yaml.safe_load(f)

        # Error handling configuration
        error_conf = self.config.get('grading', {}).get('error_handling', {})
        self.fix_latex_errors = error_conf.get('fix_latex_errors', True)
        self.latex_fix_max_retries = error_conf.get('latex_fix_max_retries', 2)

        # Late penalty configuration
        late_conf = self.config.get('grading', {}).get('late_penalties', {})
        self.late_penalties_enabled = late_conf.get('enabled', False)
        self.penalty_per_day = late_conf.get('penalty_per_day', 0.20)
        self.due_date_str = late_conf.get('due_date', None)
        self.points_awarded_elsewhere = late_conf.get('points_awarded_elsewhere', 0)

        # Initialize OpenAI client for LaTeX error fixing
        if self.fix_latex_errors:
            # Use the grader model for fixing (it's smarter), on the grader's backend
            grader_conf = self.config['system']['grader']
            self.fixer_backend = str(grader_conf.get('backend', 'ollama')).lower()
            self.raw_client = OpenAI(
                base_url=(grader_conf.get('base_url') if self.fixer_backend == 'openai'
                          else None) or self.config['system']['ollama_host'],
                api_key=grader_conf.get('api_key', 'ollama')
            )
            self.fixer_model = grader_conf['model_name']
            self.fixer_options = grader_conf.get('ollama_options', {})

        # Use raw strings to prevent SyntaxWarnings
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(self.template_dir),
            block_start_string=r'\BLOCK{',
            block_end_string=r'}',
            variable_start_string=r'\VAR{',
            variable_end_string=r'}',
            comment_start_string=r'\#{',
            comment_end_string=r'}',
            line_statement_prefix=r'%%',
            line_comment_prefix=r'%#',
            trim_blocks=True,
            autoescape=False,
        )
        self.env.filters['latex_escape'] = self.escape_latex

    def _load_grading_metadata(self):
        """Loads existing grading metadata or returns empty dict"""
        if self.metadata_path.exists():
            with open(self.metadata_path, 'r') as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save_grading_metadata(self):
        """Saves grading metadata to file"""
        with open(self.metadata_path, 'w') as f:
            yaml.dump(self.grading_metadata, f, default_flow_style=False, sort_keys=False)

    def _load_original_metadata(self):
        """Load original grading metadata for corrections mode."""
        original_path = Path(f"data/output/{self.base_assignment}_Feedback/grading_metadata.yaml")
        with open(original_path, 'r') as f:
            self.original_metadata = yaml.safe_load(f) or {}
        print(f"✓ Loaded original metadata for {len(self.original_metadata)} students")

    def calculate_late_penalty(self, submission_created_at: str, total_score: float, max_score: float, extension_hours: float = 0) -> dict:
        """
        Calculates late penalty based on submission timestamp.

        Args:
            submission_created_at: Timestamp from Gradescope (GMT/UTC)
            total_score: Total score earned (before penalty)
            max_score: Maximum possible score for the assignment
            extension_hours: Number of hours to extend deadline for this student (default: 0)

        Returns:
            dict with keys:
                - 'is_late': bool
                - 'days_late': int
                - 'penalty_percentage': float (e.g., 0.40 for 40%)
                - 'points_deducted': float (penalty applied to max_score, not earned score)
                - 'final_score': float
        """
        # Default: no penalty
        result = {
            'is_late': False,
            'days_late': 0,
            'penalty_percentage': 0.0,
            'points_deducted': 0.0,
            'final_score': total_score
        }

        # Check if late penalties are enabled
        if not self.late_penalties_enabled or not self.due_date_str:
            return result

        # Check if we have valid timestamp
        if not submission_created_at or submission_created_at == "Unknown":
            return result

        try:
            from datetime import datetime, timedelta

            # Parse due date (format: "2026-01-17 07:59:59")
            base_due_date = datetime.strptime(self.due_date_str, "%Y-%m-%d %H:%M:%S")

            # Adjust due date if student has extension
            if extension_hours > 0:
                due_date = base_due_date + timedelta(hours=extension_hours)
                print(f"   📅 Extension: +{extension_hours}h → new due date: {due_date}")
            else:
                due_date = base_due_date

            # Parse submission timestamp (Gradescope format: "2026-01-17 04:15:54.397486000 Z")
            # Strip microseconds and timezone indicator
            submission_dt = datetime.strptime(submission_created_at.split('.')[0], "%Y-%m-%d %H:%M:%S")

            # Calculate time difference
            time_diff = submission_dt - due_date

            # If submitted before or exactly at deadline, no penalty
            if time_diff.total_seconds() <= 0:
                return result

            # Calculate days late (round UP - any fraction of a day counts as full day)
            # 24 hours and 1 second = 2 days late
            days_late = int(time_diff.total_seconds() / 86400) + 1  # Always round up

            # Calculate penalty based on TOTAL maximum score (rubric + manual grading)
            # e.g., 20% of (50 rubric + 10 plots) = 12 points deducted per day
            penalty_percentage = min(days_late * self.penalty_per_day, 1.0)  # Cap at 100%
            total_max_score = max_score + self.points_awarded_elsewhere
            points_deducted = total_max_score * penalty_percentage
            final_score = total_score - points_deducted  # Allow negative scores

            result = {
                'is_late': True,
                'days_late': days_late,
                'penalty_percentage': penalty_percentage,
                'points_deducted': points_deducted,
                'final_score': final_score
            }

            return result

        except Exception as e:
            print(f"   ⚠️  Warning: Could not calculate late penalty: {e}")
            return result

    def should_skip_student(self, student_name: str, submission_created_at: str) -> bool:
        """
        Checks if a student should be skipped based on whether a feedback PDF
        already exists. If a PDF exists, the student is skipped unless their
        .tex submission file is newer than the existing feedback report.

        Args:
            student_name: Student's name
            submission_created_at: Timestamp of current submission (unused, kept for API compat)

        Returns:
            True if student should be skipped, False otherwise
        """
        safe_name = student_name.replace(" ", "_").replace("(", "").replace(")", "")
        feedback_pdf = self.output_dir / f"Feedback_{safe_name}.pdf"

        return feedback_pdf.exists()

    def _get_version_suffix(self, safe_name: str, submission_created_at: str) -> str:
        """
        Determines version suffix for report filename based on submission timestamp.

        Returns:
            "" for first submission or unchanged resubmission
            "_v2", "_v3", etc. for newer resubmissions
        """
        if not submission_created_at or submission_created_at == "Unknown":
            return ""

        student_key = safe_name

        # Check if we've graded this student before
        if student_key not in self.grading_metadata:
            # First time grading this student - no suffix needed
            self.grading_metadata[student_key] = {
                'submission_created_at': submission_created_at,
                'version': 1,
                'graded_at': str(Path(self.output_dir).stat().st_mtime) if Path(self.output_dir).exists() else "Unknown"
            }
            self._save_grading_metadata()
            return ""

        # Student has been graded before - check if submission is newer
        previous_timestamp = self.grading_metadata[student_key].get('submission_created_at', '')

        if submission_created_at == previous_timestamp:
            # Same submission - skip (caller will handle)
            return ""

        # Parse timestamps to compare (Gradescope format: "2026-01-17 04:15:54.397486000 Z")
        try:
            from datetime import datetime
            prev_dt = datetime.strptime(previous_timestamp.split('.')[0], "%Y-%m-%d %H:%M:%S")
            curr_dt = datetime.strptime(submission_created_at.split('.')[0], "%Y-%m-%d %H:%M:%S")

            if curr_dt > prev_dt:
                # Newer submission - increment version
                new_version = self.grading_metadata[student_key].get('version', 1) + 1
                self.grading_metadata[student_key] = {
                    'submission_created_at': submission_created_at,
                    'version': new_version,
                    'graded_at': str(Path(self.output_dir).stat().st_mtime) if Path(self.output_dir).exists() else "Unknown",
                    'previous_submission': previous_timestamp
                }
                self._save_grading_metadata()
                return f"_v{new_version}"
            else:
                # Older or same submission - no versioning needed
                return ""
        except Exception as e:
            # If timestamp parsing fails, just create a new version to be safe
            print(f"   ⚠️  Warning: Could not parse timestamps for {safe_name}. Creating versioned report.")
            new_version = self.grading_metadata[student_key].get('version', 1) + 1
            self.grading_metadata[student_key]['version'] = new_version
            self._save_grading_metadata()
            return f"_v{new_version}"

    def _identify_zero_scored_items(self, grade_result) -> list:
        """
        Identify grade items/sections that scored 0 points in the grading summary table.

        This is for the corrections workflow. A "grade item" is a row in the grading
        summary table (e.g., "Problem 1 (a)", "Problem 3"). Each grade item consists
        of multiple rubric criteria. If the sum of all criteria scores is 0, that
        grade item is marked as zero-scored and cannot recover points in corrections.

        Args:
            grade_result: The GradeResult object containing grading items

        Returns:
            List like ["1a", "2b", "3"] where each entry is a grade item that scored 0
        """
        zero_items = []

        # Group items by problem_group (which corresponds to grade items/table rows)
        grade_item_scores = {}
        for item in grade_result.items:
            grade_item = item.problem_group  # e.g., "Problem 1 (a)", "Problem 3"

            if grade_item not in grade_item_scores:
                grade_item_scores[grade_item] = {'score': 0, 'max': 0}

            # Only count regular items (not extra credit) for zero-score tracking
            if not getattr(item, 'is_extra_credit', False):
                grade_item_scores[grade_item]['score'] += item.score
                grade_item_scores[grade_item]['max'] += item.max_score

        # Check each grade item - if total score is 0, extract identifier
        import re
        for grade_item, data in grade_item_scores.items():
            if data['max'] > 0 and data['score'] == 0:
                # Extract identifier from grade item title
                # "Problem 1 (a)" -> "1a"
                # "Problem 2 (b)" -> "2b"
                # "Problem 3" -> "3"
                match = re.match(r'Problem\s+(\d+)\s*(?:\(([a-z])\))?', grade_item, re.IGNORECASE)
                if match:
                    problem_num = match.group(1)
                    criterion_letter = match.group(2)  # May be None

                    if criterion_letter:
                        zero_items.append(f"{problem_num}{criterion_letter}")
                    else:
                        zero_items.append(problem_num)

        return zero_items

    def _extract_extra_credit_metadata(self, grade_result) -> dict:
        """
        Extract extra credit awarded for storage in metadata.

        This is for the corrections workflow. Extra credit can only be earned in the
        first grading pass, so we save which extra credit points were awarded and
        restore them in corrections mode without re-grading.

        Args:
            grade_result: The GradeResult object containing grading items

        Returns:
            Dict mapping problem_group -> awarded points:
            {
                "Problem 1 (b)": 2.0,  # Earned 2.0 extra credit points
                "Problem 3": 1.0       # Earned 1.0 extra credit points
            }
        """
        extra_credit_earned = {}

        for item in grade_result.items:
            # Only track items that are extra credit AND have points awarded
            if getattr(item, 'is_extra_credit', False) and item.score > 0:
                problem_group = item.problem_group

                # Accumulate points if multiple extra credit items in same group
                if problem_group not in extra_credit_earned:
                    extra_credit_earned[problem_group] = 0.0

                extra_credit_earned[problem_group] += item.score

        return extra_credit_earned

    def escape_latex(self, text):
        """
        Escapes special characters and fixes common LLM Unicode mistakes
        so they compile correctly in pdflatex.
        """
        if not isinstance(text, str):
            return text
            
        # 1. Map Unicode/Symbols -> MATH MODE LaTeX
        # We wrap these in $...$ because commands like \ll crash in text mode.
        math_replacements = {
            # Greek
            'α': r'$\alpha$', 'β': r'$\beta$', 'γ': r'$\gamma$', 'π': r'$\pi$',
            'θ': r'$\theta$', 'λ': r'$\lambda$', 'ρ': r'$\rho$', 'σ': r'$\sigma$',
            
            # Operators
            '≤': r'$\le$', '≥': r'$\ge$', '≈': r'$\approx$', '≠': r'$\ne$',
            '×': r'$\times$', '·': r'$\cdot$', 
            '<<': r'$\ll$', '>>': r'$\gg$',  # Fixes the "f<<1" crash
            
            # Subscripts (The LLM loves these)
            '₀': r'$_0$', '₁': r'$_1$', '₂': r'$_2$', '₃': r'$_3$', '₄': r'$_4$',
            '₅': r'$_5$', '₆': r'$_6$', '₇': r'$_7$', '₈': r'$_8$', '₉': r'$_9$',
            
            # Superscripts
            '⁰': r'$^0$', '¹': r'$^1$', '²': r'$^2$', '³': r'$^3$', '⁴': r'$^4$',
            '⁻': r'$^-$', '⁺': r'$^+$', '°': r'$^{\circ}$',
        }
        
        for char, latex in math_replacements.items():
            text = text.replace(char, latex)

        # 2. Escape Standard LaTeX Special Chars
        # We use a loop to avoid escaping the backslashes we just added above.
        # This is a simple safe-guard: only escape characters that aren't part of a command.
        # Given the complexity, we'll just escape the dangerous text characters 
        # that likely aren't part of our math replacements (which use \ and $).
        
        # We only escape & and % and # if they are NOT inside a math block we just made.
        # To be robust but simple: just replace the ones that matter for text.
        
        # 'replace' works left-to-right. We already inserted $ and \ above.
        # We need to be careful not to escape the \ in $\alpha$.
        
        # Strategy: Tokenize by $ to separate "Math we just made" from "Text to escape"
        parts = text.split('$')
        for i, part in enumerate(parts):
            # Even indices are TEXT (outside dollar signs). Odd indices are MATH.
            if i % 2 == 0:
                # Escape special LaTeX chars in text segments
                part = part.replace('\\', r'\textbackslash{}') # Escape backslashes first
                part = part.replace('&', r'\&')
                part = part.replace('%', r'\%')
                part = part.replace('#', r'\#')
                part = part.replace('_', r'\_')
                part = part.replace('{', r'\{')
                part = part.replace('}', r'\}')
                # Replace common Unicode characters that cause LaTeX errors
                part = part.replace('Δ', r'$\Delta$')  # Greek Delta
                part = part.replace('\u202f', ' ')  # Narrow no-break space → regular space
                part = part.replace('\u2212', '-')  # Unicode minus sign → regular hyphen
                part = part.replace('∞', r'$\infty$')  # Infinity symbol
                part = part.replace('\u221e', r'$\infty$')  # Infinity symbol (alternate)
                part = part.replace('∂', r'$\partial$')  # Partial derivative
                part = part.replace('\u2202', r'$\partial$')  # Partial derivative (alternate)
                parts[i] = part

        return "$".join(parts)

    def _validate_llm_latex_fix(self, original_tex: str, fixed_tex: str, error_log: str) -> tuple[bool, str]:
        """
        Validates that the LLM's fix is actually a LaTeX document and not hallucinated content.

        Returns:
            (is_valid, reason) - True if valid fix, False with reason if invalid
        """
        # Check 1: Output must be LaTeX (not prose about clean energy, etc.)
        # A valid fix should contain key LaTeX markers
        required_markers = [
            r'\\documentclass',
            r'\\begin\{document\}',
            r'\\end\{document\}'
        ]

        for marker in required_markers:
            if not re.search(marker, fixed_tex):
                return False, f"Missing required LaTeX marker: {marker}"

        # Check 2: Length should be similar to original (within 50% difference)
        # If LLM hallucinated completely different content, length will be very different
        orig_len = len(original_tex)
        fixed_len = len(fixed_tex)
        ratio = fixed_len / orig_len if orig_len > 0 else 0

        if ratio < 0.5 or ratio > 2.0:
            return False, f"Length mismatch: original={orig_len}, fixed={fixed_len} (ratio={ratio:.2f})"

        # Check 3: Must contain feedback-related content (Jinja2 variables or section headers)
        feedback_indicators = [
            r'\\VAR\{',  # Jinja2 variables
            r'\\section',  # Section headers
            r'\\subsection',  # Subsection headers
            r'\\textbf\{Score\}',  # Score display
            r'\\textbf\{Problem',  # Problem headers
        ]

        has_feedback_content = any(re.search(indicator, fixed_tex) for indicator in feedback_indicators)
        if not has_feedback_content:
            return False, "Missing feedback content indicators (no Jinja2 vars or section headers)"

        # Check 4: Should not contain hallucinated topic keywords
        # Common hallucination topics when LLM gives up
        hallucination_keywords = [
            r'\b(renewable energy|solar power|wind power|clean energy|sustainability)\b',
            r'\b(climate change|carbon emissions|greenhouse gas)\b',
            r'\b(machine learning|neural network|artificial intelligence)\b',
            r'\b(blockchain|cryptocurrency|bitcoin)\b',
            # Add more as you discover patterns
        ]

        for keyword_pattern in hallucination_keywords:
            # Only flag if the keyword appears multiple times (once might be legitimate student content)
            matches = re.findall(keyword_pattern, fixed_tex, re.IGNORECASE)
            if len(matches) > 2:
                return False, f"Detected hallucinated content: '{matches[0]}' appears {len(matches)} times"

        # Check 5: Error log mentions specific problems - fixed tex should address them
        # Parse error types from log
        if "Undefined control sequence" in error_log or "Missing" in error_log:
            # Fixed version should have similar backslash command count as original
            orig_commands = len(re.findall(r'\\[a-zA-Z]+', original_tex))
            fixed_commands = len(re.findall(r'\\[a-zA-Z]+', fixed_tex))

            # Should be within 20% of each other
            command_ratio = fixed_commands / orig_commands if orig_commands > 0 else 0
            if command_ratio < 0.8 or command_ratio > 1.2:
                return False, f"LaTeX command count drastically changed: {orig_commands} → {fixed_commands}"

        return True, "Valid LaTeX fix"

    def _fix_latex_with_llm(self, tex_content: str, error_log: str, attempt: int) -> str:
        """
        Uses an LLM to fix LaTeX compilation errors in the feedback content.
        Only modifies the content fields (student_hint, audit_reasoning, etc.) not the structure.
        """
        system_prompt = (
            "You are a LaTeX expert. Your job is to fix LaTeX compilation errors in a feedback document.\n\n"
            "CRITICAL RULES:\n"
            "1. ONLY fix LaTeX formatting errors - DO NOT change the content or meaning\n"
            "2. DO NOT modify the document structure, commands, or anything outside the \\VAR{} blocks\n"
            "3. The errors are typically in student feedback text that contains:\n"
            "   - Unescaped special characters (%, &, #, _, {, })\n"
            "   - Incorrect math mode usage (e.g., \\ll outside math mode)\n"
            "   - Unicode characters that should be LaTeX commands\n"
            "   - Badly formed LaTeX commands\n"
            "4. Focus on fixing the text content within Jinja2 variables (between \\VAR{ and })\n"
            "5. Return the ENTIRE fixed .tex file - do not truncate or summarize\n"
            "6. Do not add any explanations, just output the fixed LaTeX code\n"
            "7. If you cannot fix the errors, return the ORIGINAL document unchanged\n\n"
            f"Attempt {attempt + 1}/{self.latex_fix_max_retries}\n"
        )

        user_prompt = (
            "Here is a LaTeX document that is failing to compile:\n\n"
            "--- LaTeX ERRORS FROM LOG ---\n"
            f"{error_log}\n\n"
            "--- FULL .tex FILE ---\n"
            f"{tex_content}\n\n"
            "Please fix ONLY the LaTeX formatting errors without changing the content meaning. "
            "Return the complete fixed .tex file."
        )

        try:
            response = self.raw_client.chat.completions.create(
                model=self.fixer_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,  # Low temperature for precise fixes
                timeout=300,  # 5-minute timeout for LLM fix
                # Ollama takes its options object; an OpenAI-compatible server takes max_tokens
                extra_body=({"options": {
                    "num_ctx": 65536,  # Large context for full .tex file
                    "num_predict": 16384,
                    "temperature": 0.1
                }} if self.fixer_backend == 'ollama' else {"max_tokens": 16384})
            )

            fixed_content = response.choices[0].message.content.strip()

            # Extract LaTeX from potential markdown code blocks
            if '```' in fixed_content:
                # Extract content between ```latex ... ``` or ``` ... ```
                pattern = r'```(?:latex|tex)?\s*(.*?)\s*```'
                match = re.search(pattern, fixed_content, re.DOTALL)
                if match:
                    fixed_content = match.group(1).strip()

            # CRITICAL: Validate the LLM's fix before using it
            is_valid, reason = self._validate_llm_latex_fix(tex_content, fixed_content, error_log)

            if not is_valid:
                print(f"   ⚠️  LLM fix validation failed: {reason}")
                print(f"   ⚠️  Reverting to original document")
                return tex_content  # Return original if fix is invalid

            print(f"   ✓ LLM fix validated successfully")
            return fixed_content

        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                print(f"   ⚠️  LLM fix timed out after 5 minutes - reverting to original document")
            else:
                print(f"   ⚠️  LLM failed to fix LaTeX: {e}")
            return tex_content  # Return original on error

    def _parse_latex_log(self, log_path: Path) -> str:
        """
        Parses a LaTeX log file to extract relevant error messages.
        Returns a condensed error summary suitable for feeding to an LLM.
        """
        if not log_path.exists():
            return "Log file not found."

        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()

        # Extract error lines (lines starting with '!')
        error_lines = []
        lines = log_content.split('\n')

        for i, line in enumerate(lines):
            if line.startswith('!'):
                # Capture the error and 2-3 lines of context
                context_start = max(0, i - 2)
                context_end = min(len(lines), i + 4)
                error_block = '\n'.join(lines[context_start:context_end])
                error_lines.append(error_block)

        if not error_lines:
            # Look for warnings that might be relevant
            warning_pattern = r'(Missing|Undefined|Overfull|Underfull|LaTeX Warning)'
            warnings = [line for line in lines if re.search(warning_pattern, line)]
            if warnings:
                return "No explicit errors found, but warnings detected:\n" + '\n'.join(warnings[:10])
            return "No clear errors found in log file."

        # Limit to first 5 errors to avoid overwhelming the LLM
        return '\n\n'.join(error_lines[:5])

    def _cleanup_aux_files(self, filename: str, success: bool):
        """
        Cleans up LaTeX auxiliary files after compilation.
        On success: moves .log to logs/ and deletes other aux files.
        On failure: moves all aux files to logs/ for debugging.
        """
        for ext in self.LATEX_AUX_EXTENSIONS:
            aux_file = self.output_dir / f"{filename}{ext}"
            if aux_file.exists():
                if ext == '.log' or not success:
                    # Keep logs (always) and all aux files on failure
                    dest = self.logs_dir / f"{filename}{ext}"
                    shutil.move(str(aux_file), str(dest))
                else:
                    # Delete other aux files on success
                    aux_file.unlink()

        # Also clean up .tex source file on success (PDF is the deliverable)
        tex_file = self.output_dir / f"{filename}.tex"
        if tex_file.exists():
            if success:
                # Move .tex to logs for reference
                shutil.move(str(tex_file), str(self.logs_dir / f"{filename}.tex"))
            # On failure, keep .tex in output dir for easier debugging

    def _compile_pdf(self, tex_path: Path, filename: str) -> bool:
        """
        Attempts to compile a .tex file to PDF using pdflatex.
        Returns True on success, False on failure.
        """
        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", str(self.output_dir), str(tex_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60  # 60 second timeout
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        except FileNotFoundError:
            print("   ❌ pdflatex not found on PATH -- cannot compile feedback PDFs. "
                  "Install a LaTeX distribution (e.g. MacTeX) or add it to PATH.")
            return False

    def generate_no_original_submission_report(self, student_name: str, student_id: str, submission_created_at: str = None):
        """
        Generates a placeholder PDF for corrections mode when student had no original submission.

        Args:
            student_name: The student's name
            student_id: The student's ID
            submission_created_at: Timestamp of corrections submission

        Returns:
            Path to generated PDF, or None if failed
        """
        safe_name = student_name.replace(" ", "_").replace("(", "").replace(")", "")
        filename = f"Feedback_{safe_name}"
        tex_path = self.output_dir / f"{filename}.tex"
        pdf_path = self.output_dir / f"{filename}.pdf"

        # Create simple LaTeX document
        tex_content = r'''\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage{geometry}
\usepackage{xcolor}
\usepackage{parskip}

\geometry{a4paper, margin=1in}

\definecolor{warning_red}{RGB}{220, 53, 69}
\definecolor{warning_bg}{RGB}{255, 243, 245}

\title{Feedback Report: ''' + self.escape_latex(student_name) + r''' (''' + student_id + r''')}
\date{\today}
\author{LaTA Evaluation System}

\begin{document}

\maketitle

\vspace{2cm}

\begin{center}
\fcolorbox{warning_red}{warning_bg}{
    \parbox{0.85\linewidth}{
        \vspace{1em}
        \centering
        \textcolor{warning_red}{\textbf{\LARGE NO ORIGINAL SUBMISSION}}

        \vspace{1em}
        \hrule
        \vspace{1em}

        \large
        You submitted a corrections assignment, but there is no record of an original submission for the base assignment.

        \vspace{1em}

        \textbf{Corrections submissions are only valid if you submitted the original assignment.}

        \vspace{1em}

        If you believe this is an error, please contact your instructor immediately.

        \vspace{1em}

        \textbf{Score: 0 / 0}

        \vspace{1em}
    }
}
\end{center}

\vfill

\footnotesize{Generated by LaTA.}

\end{document}
'''

        # Write tex file
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)

        # Compile to PDF
        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", str(self.output_dir), str(tex_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False
            )

            if result.returncode == 0 and pdf_path.exists():
                # Clean up auxiliary files
                self._cleanup_aux_files(filename, success=True)
                return pdf_path
            else:
                return None

        except Exception as e:
            print(f"   ❌ Error compiling placeholder report: {e}")
            return None

    def generate_report(self, grade_result, fix_attempt: int = 0, tex_content: str = None, submission_created_at: str = None, force_regrade: bool = False, segmentation_warning: bool = False, extension_hours: float = 0, _filename: str = None):
        """
        Generates a PDF report from grading results.

        Args:
            grade_result: The GradeResult object containing all grading data
            fix_attempt: Internal counter for LaTeX fix attempts
            tex_content: Pre-rendered TeX content (used internally for retries)
            submission_created_at: Timestamp of submission (for versioning resubmissions)
            force_regrade: If True, skips versioning and overwrites existing report
            segmentation_warning: If True, adds warning about LaTeX file issues to feedback
            _filename: Internal. Report filename (without extension) fixed by the first
                attempt, so LaTeX-fix retries write to the same file.
        """
        # Use student_name for filename if available, otherwise fall back to student_id
        if grade_result.student_name:
            safe_name = grade_result.student_name.replace(" ", "_").replace("(", "").replace(")", "")
        else:
            safe_name = grade_result.student_id.replace(" ", "_").replace("(", "").replace(")", "")

        # Check for existing reports and determine version
        # Skip versioning if force_regrade is True.
        # A LaTeX-fix retry must reuse the first attempt's filename rather than ask for the
        # version again: _get_version_suffix records the new submission timestamp on its
        # first call, so a second call sees "same submission" and returns "" -- which wrote
        # a resubmission's fixed report over the student's original Feedback_<name>.pdf.
        if _filename is not None:
            filename = _filename
        else:
            if force_regrade:
                version_suffix = ""
            else:
                version_suffix = self._get_version_suffix(safe_name, submission_created_at)
            filename = f"Feedback_{safe_name}{version_suffix}"
        tex_path = self.output_dir / f"{filename}.tex"
        pdf_path = self.output_dir / f"{filename}.pdf"

        # Only render template on first attempt
        if tex_content is None:
            template = self.env.get_template("feedback_report.tex")

            # In corrections mode, restore original extra credit
            if self.is_corrections_mode:
                safe_name = grade_result.student_name.replace(" ", "_").replace("(", "").replace(")", "") if grade_result.student_name else grade_result.student_id.replace(" ", "_").replace("(", "").replace(")", "")
                student_orig = self.original_metadata.get(safe_name, {})
                orig_extra_credit = student_orig.get('extra_credit_earned', {})

                if orig_extra_credit:
                    print(f"   🌟 Restoring original extra credit: {orig_extra_credit}")

                    # Import GradeItem to create synthetic items
                    from lata.grader import GradeItem

                    # Inject extra credit items back into grade_result.items
                    # Create synthetic GradeItem objects for each extra credit award
                    for problem_group, points in orig_extra_credit.items():
                        # Create a synthetic extra credit item
                        synthetic_item = GradeItem(
                            problem_group=problem_group,
                            criterion_name="Original Extra Credit (preserved)",
                            score=points,
                            max_score=0.0,  # Extra credit doesn't count toward max
                            is_extra_credit=True,
                            audit_reasoning="Preserved from original grading - extra credit cannot be re-earned",
                            student_hint="Extra credit from original submission (preserved)"
                        )

                        # Add to grade_result.items
                        grade_result.items.append(synthetic_item)

                    # Recalculate total_score to include restored extra credit
                    grade_result.total_score = sum(i.score for i in grade_result.items)

            grouped_data = {}

            for item in grade_result.items:
                group = item.problem_group
                if group not in grouped_data:
                    grouped_data[group] = {
                        'name': group,
                        'score': 0,
                        'max': 0,
                        'extra_credit_earned': 0,
                        'extra_credit_max': 0,
                        'grade_items': [],
                        'extra_credit_items': []
                    }

                grouped_data[group]['grade_items'].append(item)

                # Separate regular and extra credit items
                if getattr(item, 'is_extra_credit', False):
                    grouped_data[group]['extra_credit_earned'] += item.score
                    grouped_data[group]['extra_credit_max'] += item.max_score
                    grouped_data[group]['extra_credit_items'].append(item)
                else:
                    grouped_data[group]['score'] += item.score
                    grouped_data[group]['max'] += item.max_score

            # Calculate displayed scores and deducted points for each group
            for group_data in grouped_data.values():
                group_data['total_displayed_score'] = group_data['score'] + group_data['extra_credit_earned']
                group_data['deducted'] = group_data['max'] - group_data['score']  # Deducted from regular only

            summary_table = list(grouped_data.values())

            # Calculate maximum possible score (excludes extra credit)
            max_score = sum(item.max_score for item in grade_result.items
                            if not getattr(item, 'is_extra_credit', False))

            # Calculate extra credit earned
            total_extra_credit = sum(item.score for item in grade_result.items
                                     if getattr(item, 'is_extra_credit', False))

            # Calculate total deducted points (from regular points only)
            regular_score = grade_result.total_score - total_extra_credit
            total_deducted = max_score - regular_score

            # Late penalty calculation - use original if corrections mode
            if self.is_corrections_mode:
                # Lookup original penalty from metadata
                safe_name = grade_result.student_name.replace(" ", "_").replace("(", "").replace(")", "") if grade_result.student_name else grade_result.student_id.replace(" ", "_").replace("(", "").replace(")", "")
                student_orig = self.original_metadata.get(safe_name, {})
                orig_penalty_percentage = student_orig.get('late_penalty', 0.0)
                zero_scored_items = student_orig.get('zero_scored_items', [])

                # Reconstruct late_penalty dict in same format as calculate_late_penalty()
                # Must use total_max_score to match normal mode behavior
                if orig_penalty_percentage > 0:
                    total_max_score = max_score + self.points_awarded_elsewhere
                    points_deducted = orig_penalty_percentage * total_max_score
                    late_penalty = {
                        'is_late': True,
                        'days_late': int(orig_penalty_percentage / self.penalty_per_day),  # Approximate
                        'penalty_percentage': orig_penalty_percentage,
                        'points_deducted': points_deducted,
                        'final_score': grade_result.total_score - points_deducted  # Allow negative
                    }
                    print(f"   📋 Using original late penalty: {orig_penalty_percentage*100:.0f}%")
                else:
                    late_penalty = {'is_late': False, 'days_late': 0, 'penalty_percentage': 0.0, 'points_deducted': 0.0, 'final_score': grade_result.total_score}

                if zero_scored_items:
                    print(f"   🚫 Zero-scored items: {', '.join(zero_scored_items)}")
            else:
                # Normal mode: calculate penalty from timestamp
                late_penalty = self.calculate_late_penalty(submission_created_at, grade_result.total_score, max_score, extension_hours)
                # Identify zero-scored items
                zero_scored_items = self._identify_zero_scored_items(grade_result)

            context = grade_result.model_dump()
            context['grouped_data'] = grouped_data
            context['summary_table'] = summary_table
            context['max_total'] = max_score
            context['total_deducted'] = total_deducted
            context['total_extra_credit'] = total_extra_credit
            context['late_penalty'] = late_penalty
            context['segmentation_warning'] = segmentation_warning
            context['zero_scored_items'] = zero_scored_items  # For corrections workflow
            context['is_corrections_mode'] = self.is_corrections_mode

            tex_content = template.render(context)

        with open(tex_path, "w") as f:
            f.write(tex_content)

        # Save extended metadata with late penalty, zero-scored items, and extra credit
        # This must happen regardless of compilation success so corrections mode can
        # restore extra credit even for students whose LaTeX failed to compile.
        # Only save on first attempt (not on recursive LLM fix attempts)
        if fix_attempt == 0:
            safe_name = grade_result.student_name.replace(" ", "_").replace("(", "").replace(")", "") if grade_result.student_name else grade_result.student_id.replace(" ", "_").replace("(", "").replace(")", "")

            if safe_name in self.grading_metadata:
                # Extract extra credit awarded (for corrections workflow)
                extra_credit_earned = self._extract_extra_credit_metadata(grade_result)

                # Update existing metadata with new fields
                self.grading_metadata[safe_name]['late_penalty'] = late_penalty.get('penalty_percentage', 0.0)
                self.grading_metadata[safe_name]['zero_scored_items'] = zero_scored_items
                self.grading_metadata[safe_name]['extra_credit_earned'] = extra_credit_earned
                self._save_grading_metadata()

        # Attempt compilation
        compilation_successful = self._compile_pdf(tex_path, filename)

        if compilation_successful:
            # Clean up auxiliary files on success
            self._cleanup_aux_files(filename, success=True)
            return pdf_path
        else:
            # Compilation failed
            # pdflatex writes the .log next to the PDF in output_dir; _cleanup_aux_files moves
            # it to logs_dir afterwards. Parsing the logs_dir copy before that move gave the
            # LLM fixer "Log file not found." -- or a stale log from an earlier run.
            fresh_log_path = self.output_dir / f"{filename}.log"
            log_path = self.logs_dir / f"{filename}.log"

            # Check if we should attempt LLM-based fixing
            if self.fix_latex_errors and fix_attempt < self.latex_fix_max_retries:
                # Parse error log and count errors
                error_summary = self._parse_latex_log(fresh_log_path)
                error_count = error_summary.count('!')  # Count explicit error markers

                # Skip LLM fixing if there are too many errors (likely a blank/zero-credit submission)
                MAX_ERRORS_FOR_LLM_FIX = 10
                if error_count > MAX_ERRORS_FOR_LLM_FIX:
                    print(f"   ⚠️  LaTeX compilation failed with {error_count} errors (>{MAX_ERRORS_FOR_LLM_FIX})")
                    print(f"   ⚠️  Too many errors - skipping LLM fix (likely blank/zero-credit submission)")
                    self._cleanup_aux_files(filename, success=False)

                    # Save broken tex for debugging
                    backup_tex = self.logs_dir / f"{filename}_broken.tex"
                    shutil.copy(str(tex_path), str(backup_tex))

                    # Check if PDF was partially generated
                    if pdf_path.exists():
                        print(f"   ⚠️  Partial PDF generated (may contain formatting errors): {pdf_path}")
                        return pdf_path

                    return None

                print(f"   ⚠️  LaTeX compilation failed ({error_count} errors). Attempting LLM-based fix (Attempt {fix_attempt + 1}/{self.latex_fix_max_retries})...")

                # Save original tex with attempt number
                if fix_attempt > 0:
                    backup_tex = self.logs_dir / f"{filename}_broken_attempt_{fix_attempt}.tex"
                else:
                    backup_tex = self.logs_dir / f"{filename}_broken.tex"
                shutil.copy(str(tex_path), str(backup_tex))

                # Get LLM to fix the LaTeX
                fixed_tex = self._fix_latex_with_llm(tex_content, error_summary, fix_attempt)

                # Recursively retry compilation with the fixed version
                return self.generate_report(
                    grade_result,
                    fix_attempt=fix_attempt + 1,
                    tex_content=fixed_tex,
                    submission_created_at=submission_created_at,
                    force_regrade=force_regrade,
                    segmentation_warning=segmentation_warning,
                    extension_hours=extension_hours,
                    _filename=filename
                )
            else:
                # Give up after max retries or if fixing is disabled
                self._cleanup_aux_files(filename, success=False)

                if fix_attempt > 0:
                    print(f"   ❌ PDF compilation failed after {fix_attempt} fix attempt(s). Check: {log_path}")
                else:
                    print(f"   ❌ PDF compilation failed. Check: {log_path}")

                # Check if PDF was partially generated (sometimes pdflatex produces a PDF despite errors)
                if pdf_path.exists():
                    print(f"   ⚠️  Partial PDF generated (may contain formatting errors): {pdf_path}")
                    return pdf_path

                return None