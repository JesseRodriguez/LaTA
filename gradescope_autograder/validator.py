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
Gradescope Pre-Submission Validator for LaTeX Homework Submissions

Validates that student submissions meet structural requirements for the
LaTA autograder system.
"""

import json
import os
import re
import sys
import yaml
from pathlib import Path
from typing import List, Tuple, Dict, Optional


class ValidationResult:
    """Container for validation results"""
    def __init__(self, passed: bool, message: str, details: List[str] = None):
        self.passed = passed
        self.message = message
        self.details = details or []


class TexValidator:
    """Validates LaTeX homework submissions"""

    # Regex patterns matching LaTA's segmenter.py
    PROB_PATTERN = re.compile(r'\\Prob\s*\{([^{}]+)\}')
    DOCUMENT_BEGIN = re.compile(r'\\begin\{document\}')
    DOCUMENT_END = re.compile(r'\\end\{document\}')

    def __init__(self, submission_dir: Path, config: Dict = None):
        self.submission_dir = submission_dir
        self.config = config or {}
        self.tex_files = list(submission_dir.glob("*.tex"))
        self.tex_content = None
        self.preamble = None
        self.document_body = None

    def validate_all(self) -> ValidationResult:
        """Run all validation checks"""

        # Check 1: Single .tex file
        result = self.check_single_tex_file()
        if not result.passed:
            return result

        # Check 2: Read file
        result = self.read_tex_file()
        if not result.passed:
            return result

        # Check 3: Document structure
        result = self.check_document_structure()
        if not result.passed:
            return result

        # Check 4: Problem markers
        result = self.check_problem_markers()
        if not result.passed:
            return result

        # Check 5: Basic syntax
        syntax_result = self.check_basic_syntax()
        # Syntax check can produce warnings, but won't fail submission

        # All checks passed!
        success_details = [
            "Single .tex file found",
            "Valid document structure with \\begin{document}...\\end{document}",
            f"Found {len(self.get_problem_numbers())} problem markers",
            "No duplicate problem numbers",
        ]

        # Include syntax check details (may include warnings)
        if syntax_result.details:
            success_details.extend(syntax_result.details)
        else:
            success_details.append("Basic syntax checks passed")

        return ValidationResult(
            passed=True,
            message="✓ Submission validated successfully",
            details=success_details
        )

    def check_single_tex_file(self) -> ValidationResult:
        """Check that exactly one .tex file is submitted"""
        if len(self.tex_files) == 0:
            return ValidationResult(
                passed=False,
                message="✗ No .tex file found",
                details=[
                    "Your submission must include exactly one .tex file.",
                    "Make sure your file has the .tex extension.",
                    "Check that you uploaded the correct file."
                ]
            )

        if len(self.tex_files) > 1:
            return ValidationResult(
                passed=False,
                message="✗ Multiple .tex files found",
                details=[
                    f"Found {len(self.tex_files)} .tex files: {', '.join(f.name for f in self.tex_files)}",
                    "Please submit only ONE .tex file containing all your solutions.",
                    "If you're using \\input or \\include, merge all files into a single .tex file."
                ]
            )

        return ValidationResult(passed=True, message="Single .tex file found")

    def read_tex_file(self) -> ValidationResult:
        """Read the .tex file content"""
        try:
            tex_file = self.tex_files[0]
            with open(tex_file, 'r', encoding='utf-8', errors='replace') as f:
                self.tex_content = f.read()
            return ValidationResult(passed=True, message="File read successfully")
        except Exception as e:
            return ValidationResult(
                passed=False,
                message="✗ Could not read .tex file",
                details=[
                    f"Error: {str(e)}",
                    "Make sure your file is a valid text file with UTF-8 encoding."
                ]
            )

    def check_document_structure(self) -> ValidationResult:
        """Check for \\begin{document}...\\end{document} structure"""
        # Use the FIRST \begin{document} and LAST \end{document} to define the body
        # This ensures we capture the full content even if students pasted extra markers
        begin_matches = list(self.DOCUMENT_BEGIN.finditer(self.tex_content))
        end_matches = list(self.DOCUMENT_END.finditer(self.tex_content))

        if not begin_matches:
            return ValidationResult(
                passed=False,
                message="✗ Missing \\begin{document}",
                details=[
                    "Your .tex file must contain \\begin{document}.",
                    "Make sure you're using the provided template.",
                    "Check that you didn't accidentally delete the \\begin{document} line."
                ]
            )

        if not end_matches:
            return ValidationResult(
                passed=False,
                message="✗ Missing \\end{document}",
                details=[
                    "Your .tex file must contain \\end{document}.",
                    "Make sure you're using the provided template.",
                    "Check that you didn't accidentally delete the \\end{document} line."
                ]
            )

        begin_match = begin_matches[0]   # First \begin{document}
        end_match = end_matches[-1]      # Last \end{document}

        if begin_match.start() >= end_match.start():
            return ValidationResult(
                passed=False,
                message="✗ Invalid document structure",
                details=[
                    "\\end{document} appears before \\begin{document}.",
                    "Check the structure of your .tex file."
                ]
            )

        # Extract preamble and document body
        self.preamble = self.tex_content[:begin_match.start()]
        self.document_body = self.tex_content[begin_match.end():end_match.start()]

        # Check for extra \begin{document} or \end{document} inside the body
        # This happens when students copy-paste LLM output that includes document markers
        extra_begins = list(self.DOCUMENT_BEGIN.finditer(self.document_body))
        extra_ends = list(self.DOCUMENT_END.finditer(self.document_body))

        if extra_begins or extra_ends:
            extra_count = len(extra_begins) + len(extra_ends)
            return ValidationResult(
                passed=False,
                message=f"✗ Extra \\begin{{document}} or \\end{{document}} found inside your document body",
                details=[
                    f"Found {len(extra_begins)} extra \\begin{{document}} and {len(extra_ends)} extra \\end{{document}} inside the document body.",
                    "This usually happens when copying code from a chat interface (e.g., ChatGPT) that includes full LaTeX document wrappers.",
                    "The autograder cannot properly parse your submission with these extra markers.",
                    "",
                    "To fix this:",
                    "  1. Open your .tex file",
                    "  2. Search for any \\begin{document} or \\end{document} lines BETWEEN the first \\begin{document} and the final \\end{document}",
                    "  3. Delete those extra lines",
                    "  4. Also remove any extra \\documentclass or \\usepackage lines that may have been pasted into the body",
                    "  5. Re-upload your corrected file"
                ]
            )

        return ValidationResult(passed=True, message="Valid document structure")

    def get_problem_numbers(self) -> List[str]:
        """Extract problem numbers from document body"""
        matches = self.PROB_PATTERN.findall(self.document_body)

        # Filter to "top-level" matches (digits only, like LaTA's segmenter)
        top_level = []
        for match in matches:
            stripped = match.strip()
            if stripped.isdigit():
                top_level.append(stripped)

        return top_level

    def get_problem_segments(self) -> Dict[str, str]:
        """Extract individual problem segments from document body"""
        segments = {}
        problem_numbers = self.get_problem_numbers()

        if not problem_numbers:
            return segments

        # Find all \Prob{N} positions
        prob_positions = []
        for match in self.PROB_PATTERN.finditer(self.document_body):
            prob_num = match.group(1).strip()
            if prob_num.isdigit():
                prob_positions.append((prob_num, match.start()))

        # Extract content between markers
        for i, (prob_num, start_pos) in enumerate(prob_positions):
            if i + 1 < len(prob_positions):
                end_pos = prob_positions[i + 1][1]
            else:
                end_pos = len(self.document_body)

            segment_content = self.document_body[start_pos:end_pos]
            segments[prob_num] = segment_content

        return segments

    def check_problem_markers(self) -> ValidationResult:
        """Check that problem markers are present and valid"""
        problem_numbers = self.get_problem_numbers()

        if len(problem_numbers) == 0:
            return ValidationResult(
                passed=False,
                message="✗ No problem markers found",
                details=[
                    "Your submission must use \\Prob{N} markers to separate problems.",
                    "Example: \\Prob{1}, \\Prob{2}, \\Prob{3}, etc.",
                    "Make sure N is only a number (not '1a' or 'Problem 1').",
                    "Check that you're using the provided template correctly."
                ]
            )

        # Check for duplicates (matching LaTA's segmenter logic)
        seen = set()
        duplicates = []
        for num in problem_numbers:
            if num in seen:
                duplicates.append(num)
            seen.add(num)

        if duplicates:
            return ValidationResult(
                passed=False,
                message="✗ Duplicate problem numbers found",
                details=[
                    f"The following problem numbers appear multiple times: {', '.join(duplicates)}",
                    "Each problem number should appear exactly once.",
                    "Check that you didn't copy-paste \\Prob{1} when you meant \\Prob{2}."
                ]
            )

        # Check expected number of problems (if configured)
        expected_count = self.config.get('expected_problems')
        if expected_count is not None:
            if len(problem_numbers) != expected_count:
                return ValidationResult(
                    passed=False,
                    message=f"✗ Wrong number of problems",
                    details=[
                        f"Expected {expected_count} problems, but found {len(problem_numbers)}.",
                        f"Your submission has: {', '.join(problem_numbers)}",
                        f"This assignment requires exactly {expected_count} problems.",
                        "Check that you haven't missed any problems or have extra problem markers."
                    ]
                )

        # Check for specific expected problem numbers (if configured)
        expected_nums = self.config.get('expected_problem_numbers')
        if expected_nums is not None:
            problem_nums_int = [int(n) for n in problem_numbers]
            if problem_nums_int != expected_nums:
                return ValidationResult(
                    passed=False,
                    message=f"✗ Incorrect problem numbers",
                    details=[
                        f"Expected problems: {', '.join(str(n) for n in expected_nums)}",
                        f"Found problems: {', '.join(problem_numbers)}",
                        "Check that you're using the correct problem numbers for this assignment."
                    ]
                )

        # Check for sequential ordering (warning only if no strict check above)
        sorted_nums = sorted([int(n) for n in problem_numbers])
        expected = list(range(1, len(problem_numbers) + 1))

        details = [f"Found {len(problem_numbers)} problems: {', '.join(problem_numbers)}"]

        if sorted_nums != expected:
            details.append(
                f"Note: Problems are not numbered sequentially (1, 2, 3, ...). "
                f"Found: {', '.join(problem_numbers)}. This may be intentional."
            )

        return ValidationResult(
            passed=True,
            message=f"Found {len(problem_numbers)} valid problem markers",
            details=details
        )

    def check_basic_syntax(self) -> ValidationResult:
        """Basic syntax checks (warnings only, won't fail submission)"""
        details = []

        # Check balanced braces
        open_count = self.tex_content.count('{')
        close_count = self.tex_content.count('}')

        if open_count != close_count:
            details.append(
                f"⚠ Warning: Unbalanced braces detected ({open_count} open, {close_count} close). "
                f"This may cause LaTeX compilation errors."
            )

        # Check for macros defined in document body
        macro_pattern = re.compile(r'\\(?:new|renew)command')
        if macro_pattern.search(self.document_body):
            details.append(
                "⚠ Warning: Custom macros (\\newcommand, \\renewcommand) found in document body. "
                "Define macros in the preamble (before \\begin{document}) to ensure they're preserved."
            )

        # Check for common unescaped special characters
        special_chars = ['%', '&', '_', '#']
        for char in special_chars:
            # Simple heuristic: if we find the char not preceded by backslash
            pattern = f'(?<!\\\\){re.escape(char)}'
            if re.search(pattern, self.document_body):
                details.append(
                    f"⚠ Warning: Unescaped '{char}' found. Use '\\{char}' in LaTeX."
                )
                break  # Only warn once about special chars

        # Check for suspiciously short solutions (possible blank template submission)
        self._check_solution_length(details)

        if not details:
            details.append("No syntax warnings")

        return ValidationResult(passed=True, message="Basic syntax checks complete", details=details)

    def _check_solution_length(self, details: List[str]):
        """Check if solutions are suspiciously short (warning only)"""
        # Typical solution lengths (lines per problem) based on ME373 assignments
        # These are conservative estimates - most real solutions are longer
        MINIMUM_LINES_PER_PROBLEM = {
            'default': 25,  # Default minimum: ~25 lines per problem
            # Can be configured per-assignment if needed
        }

        segments = self.get_problem_segments()
        min_lines = MINIMUM_LINES_PER_PROBLEM.get('default', 25)

        short_problems = []
        for prob_num, content in segments.items():
            # Count non-empty lines (ignore whitespace-only lines)
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            # Don't count the \Prob{N} line itself
            content_lines = [line for line in lines if not line.startswith('\\Prob{')]

            if len(content_lines) < min_lines:
                short_problems.append((prob_num, len(content_lines)))

        if short_problems:
            details.append(
                f"⚠ Warning: Unusually short solutions detected:"
            )
            for prob_num, line_count in short_problems:
                details.append(
                    f"  - Problem {prob_num}: only {line_count} lines (typical solutions have {min_lines}+ lines)"
                )
            details.append(
                "  Double-check you haven't submitted a blank template or incomplete work."
            )


def generate_gradescope_json(validation_result: ValidationResult, output_path: Path):
    """Generate Gradescope results.json format"""

    # Build output message
    output_lines = [validation_result.message, ""]
    output_lines.extend(validation_result.details)
    output_text = "\n".join(output_lines)

    # Score: 1.0 if passed, 0.0 if failed
    score = 1.0 if validation_result.passed else 0.0

    # Build test case
    test_case = {
        "name": "LaTeX Submission Validation",
        "score": score,
        "max_score": 1.0,
        "output": output_text,
        "visibility": "visible"
    }

    # Add status indicator
    if validation_result.passed:
        test_case["status"] = "passed"
    else:
        test_case["status"] = "failed"

    # Build final results
    results = {
        "score": score,
        "execution_time": 0,
        "output": "Submission Validation Results",
        "visibility": "visible",
        "stdout_visibility": "hidden",
        "tests": [test_case]
    }

    # Write to file
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)


def load_config() -> Dict:
    """Load configuration from config.yaml if it exists"""
    config_path = Path(__file__).parent / "config.yaml"

    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
                return config
        except Exception as e:
            print(f"Warning: Could not load config.yaml: {e}")
            return {}

    return {}


def main():
    """Main entry point"""
    if len(sys.argv) != 3:
        print("Usage: validator.py <submission_dir> <output_json>")
        sys.exit(1)

    submission_dir = Path(sys.argv[1])
    output_json = Path(sys.argv[2])

    # Load configuration
    config = load_config()

    # Run validation
    validator = TexValidator(submission_dir, config)
    result = validator.validate_all()

    # Generate Gradescope JSON
    generate_gradescope_json(result, output_json)

    # Print to stdout (for instructor debugging)
    print(result.message)
    for detail in result.details:
        print(f"  {detail}")

    sys.exit(0)


if __name__ == '__main__':
    main()
