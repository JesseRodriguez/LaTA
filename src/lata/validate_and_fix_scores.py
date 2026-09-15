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
Score validation for LLM grading output, checked against the rubric.

Runs on the structured GradeItems for one problem, right after the LLM response is
parsed and before anything is rendered, so every total computed downstream (summary
table, late penalty, zero-scored items, corrections metadata) uses corrected values.

It only makes corrections that are unambiguous:
  - max_score is set to the rubric's point value for that criterion. A full-credit
    score moves with it; any other score is capped at the new maximum.
  - score is clamped to [0, max_score].

Everything else is reported but left unchanged, because "fixing" it would mean
guessing and could silently move a student's grade in either direction:
  - partial credit on an item (the prompt requires full or zero)
  - a criterion the rubric does not list for that section
  - a rubric criterion the LLM did not grade at all

This replaces an earlier validator that regex-matched the rendered .tex. It had
drifted out of sync with templates/feedback_report.tex, matched no items, and
reported bogus "recalculations" such as 2.0/2.0 -> 5.0/5.0; it also removed any
item whose name did not match the rubric exactly, which would have handed back the
points for that item.
"""

import re


def normalize_criterion_name(name: str) -> str:
    """Normalize a criterion name for matching LLM output against the rubric.

    Handles:
    - Trailing punctuation (periods, commas, etc.)
    - Multiple apostrophes in f_j', f_j'', f_j''', etc. -> f_j'
    - Extra descriptive text appended by the LLM ("... and state that ...")
    """
    normalized = name.rstrip('.!?;:,').strip()
    normalized = re.sub(r"f_j''+", "f_j'", normalized)
    normalized = re.sub(r'\s+and\s+(state|stated)\s+that.*$', '', normalized, flags=re.IGNORECASE)
    return normalized


def _fmt(x: float) -> str:
    return f"{x:g}"


def validate_grade_items(items, rubric_sections) -> list:
    """
    Validate and correct GradeItems for one problem against its rubric sections.

    Args:
        items: list of GradeItem (mutated in place)
        rubric_sections: the rubric entries for this problem, i.e.
            [{'title': 'Problem 1 (a)', 'items': [{'name': ..., 'points': ...}, ...]}, ...]

    Returns:
        list of (kind, message) tuples; kind is 'fix' for a correction that was
        applied, or 'note' for something reported but left unchanged.
    """
    findings = []

    rubric_points = {}
    for section in rubric_sections:
        title = section.get('title', '')
        for r_item in section.get('items', []):
            key = (title, normalize_criterion_name(str(r_item.get('name', ''))))
            try:
                rubric_points[key] = float(r_item.get('points'))
            except (TypeError, ValueError):
                continue

    graded_keys = set()
    for item in items:
        key = (item.problem_group, normalize_criterion_name(item.criterion_name))
        graded_keys.add(key)
        label = f"{item.problem_group}: {item.criterion_name[:60]}"

        if key not in rubric_points:
            findings.append(('note', f"criterion not in rubric for this section, left as graded -- {label}"))
        else:
            points = rubric_points[key]
            if abs(item.max_score - points) > 1e-6:
                old_score, old_max = item.score, item.max_score
                item.max_score = points
                item.score = points if old_score >= old_max else min(old_score, points)
                findings.append(('fix', f"max_score {_fmt(old_max)} -> {_fmt(points)} (rubric), "
                                        f"score {_fmt(old_score)} -> {_fmt(item.score)} -- {label}"))

        if item.score > item.max_score + 1e-6:
            findings.append(('fix', f"score {_fmt(item.score)} exceeds max {_fmt(item.max_score)}, capped -- {label}"))
            item.score = item.max_score
        if item.score < 0:
            findings.append(('fix', f"negative score {_fmt(item.score)} set to 0 -- {label}"))
            item.score = 0.0
        if 1e-6 < item.score < item.max_score - 1e-6:
            findings.append(('note', f"partial credit {_fmt(item.score)}/{_fmt(item.max_score)} "
                                     f"(rubric is full-or-zero), left as graded -- {label}"))

    for key in rubric_points:
        if key not in graded_keys:
            findings.append(('note', f"rubric criterion not graded by the LLM -- {key[0]}: {key[1][:60]}"))

    return findings
