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
Gradescope as the watcher's source of submissions, and as where its grades go.

Source. Each pass downloads the grades CSV of each assignment's .tex and PDF assignments
(one small request each). The PDF assignment is only used for submission times, and the
CSV has them, so its submissions are never downloaded. The .tex assignment is exported
("Export Submissions") only when its list of submissions has changed.

Grades. A graded student's score goes into the PDF assignment's LaTA question as you
would enter it by hand: on a negatively scored question, LaTA's points off (out of its
rubric total, late penalty included) as the point adjustment, or the zero-point "Correct"
rubric item for a perfect score. The LaTA question is the PDF assignment's largest
question unless gradescope.question names it; the rest are left for hand grading. A student who
submitted a PDF but no .tex gets the rubric item that takes all points away (or an
adjustment of minus the question's points). Grades are only written to question
submissions nobody has graded -- or that LaTA graded itself -- and are read back after
saving. Corrections go to the student's new (resubmitted, ungraded) PDF submission.

Release. Feedback waits until you have graded the hand-graded questions and published
the grades on Gradescope. Then `send` (typed into the watcher) checks that the assignment
is published and every waiting student is fully graded, posts grades to Canvas, and
emails the feedback.
"""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from lata.gradescope import Gradescope, GradescopeError, NotLoggedIn
from lata.inbox import prepare_from_inbox
from lata.ledger import gradescope_ts, iso, parse_ts, student_key

STATE = ".gradescope.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def row_key(row: Dict[str, str]) -> str:
    return student_key(row.get('SID'), row.get('Email'))


def submitted(row: Dict[str, str]) -> bool:
    return bool(row.get('Submission ID')) and row.get('Status') != 'Missing'


def pdf_metadata_from_scores(rows: List[Dict[str, str]]) -> dict:
    """The PDF assignment's submission_metadata.yml, rebuilt from its grades CSV.

    Times are written in Gradescope's export format (UTC, "... Z"), which is what the
    ingestor and post_grading.py parse."""
    meta = {}
    for r in rows:
        if not submitted(r):
            continue
        created = parse_ts(r.get('Submission Time'))
        meta[f"submission_{r['Submission ID']}"] = {
            ':submitters': [{':name': f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip(),
                             ':sid': r.get('SID') or None, ':email': r.get('Email')}],
            ':created_at': gradescope_ts(created) if created else r.get('Submission Time'),
        }
    return meta


class GradescopeSource:
    """Refreshes an assignment's export directory from Gradescope."""

    def __init__(self, gs: Gradescope, course: int, inbox_dir: str):
        self.gs = gs
        self.course = course
        self.root = Path(inbox_dir)
        self.pdf_rows: Dict[str, List[Dict[str, str]]] = {}
        self._reported = set()

    def describe(self, a) -> str:
        ids = a.gradescope or {}
        return f"Gradescope course {self.course}, .tex {ids.get('tex')}, PDF {ids.get('pdf')}"

    def refresh(self, a, events) -> None:
        ids = a.gradescope
        state_path = a.export_dir / STATE
        state = _load(state_path)

        pdf_rows = self.gs.scores(self.course, ids['pdf'])
        self.pdf_rows[a.name] = pdf_rows
        a.export_dir.mkdir(parents=True, exist_ok=True)
        meta = pdf_metadata_from_scores(pdf_rows)
        meta_path = a.export_dir / "pdf_submission_metadata.yml"
        new_text = yaml.safe_dump(meta, sort_keys=True)
        if not meta_path.exists() or meta_path.read_text() != new_text:
            meta_path.write_text(new_text)

        tex_rows = self.gs.scores(self.course, ids['tex'])
        signature = sorted(r['Submission ID'] for r in tex_rows if submitted(r))
        if signature and signature != state.get('tex_signature'):
            folder = self.root / a.name
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            zip_path = folder / f"gradescope_tex_{stamp}.zip"
            self.gs.export_submissions(self.course, ids['tex'], zip_path)
            result = prepare_from_inbox(folder, a.export_dir, quiet=True)
            for action in (result.actions if result else []):
                events.info.append(f"{a.name}: {action}")
            for old in folder.glob("gradescope_tex_*.zip"):        # keep only the newest
                if old != zip_path:
                    old.unlink(missing_ok=True)
            state['tex_signature'] = signature
            _save(state_path, state)

        # After the export, so names match the export's spelling
        try:
            self.sync_extensions(a, pdf_rows, events)
        except GradescopeError as e:
            self._once(events, f"{a.name}: could not read extensions from Gradescope: {e}")

    def _once(self, events, message: str):
        if message not in self._reported:
            self._reported.add(message)
            events.attention.append(message)


    def sync_extensions(self, a, pdf_rows: List[Dict[str, str]], events) -> None:
        """Write the extensions entered on Gradescope (on either the .tex or the PDF
        assignment) to <export_dir>/extensions.gradescope.yaml, in hours past the config's
        due date, by the student's name as it appears in the export. A student extended on
        both assignments gets the later date."""
        from lata.ingest import EXTENSIONS_FILES
        from lata.setup_watch import to_utc
        tz = self.gs.course_timezone(self.course)
        # Names as LaTA knows them (the .tex export), so extensions match by name downstream
        names = {}
        meta_path = a.export_dir / "submission_metadata.yml"
        if meta_path.exists():
            meta = yaml.safe_load(meta_path.read_text()) or {}
            for sub in meta.values():
                for person in (sub or {}).get(':submitters') or (sub or {}).get('submitters') or []:
                    email = person.get(':email', person.get('email'))
                    if email:
                        names.setdefault(str(email).lower(), person.get(':name', person.get('name')))
        for r in pdf_rows:
            if r.get('Email'):
                names.setdefault(r['Email'].lower(), f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip())

        hours, odd = {}, []
        for assignment in dict.fromkeys((a.gradescope['tex'], a.gradescope['pdf'])):
            for ext in self.gs.extensions(self.course, assignment):
                name = names.get(str(ext.get('email') or '').lower()) or ext.get('name')
                if ext.get('type') != 'absolute' or not ext.get('due'):
                    odd.append(name or ext.get('user_id'))
                    continue
                extra = (parse_ts(to_utc(ext['due'], tz)) - a.due).total_seconds() / 3600
                if extra > 0 and name:
                    hours[name] = max(hours.get(name, 0.0), round(extra, 2))
        for name in odd:
            self._once(events, f"{a.name}: {name}: extension on Gradescope is not a fixed date; "
                               f"put it in {EXTENSIONS_FILES[0]} instead")
        path = a.export_dir / EXTENSIONS_FILES[1]
        text = yaml.safe_dump(hours, sort_keys=True) if hours else ""
        previous = path.read_text() if path.exists() else ""
        if text != previous:
            if hours:
                path.write_text(text)
            elif path.exists():
                path.unlink()
            events.info.append(f"{a.name}: extensions from Gradescope: {len(hours)} student(s)")


@dataclass
class Entry:
    status: str       # entered | match | would-enter | conflict | no-pdf | no-score | error
    message: str
    record: Optional[dict] = None


class GradeEntry:
    """Writes LaTA's grades into the PDF assignment's LaTA question."""

    def __init__(self, gs: Gradescope, course: int):
        self.gs = gs
        self.course = course
        self.questions: Dict[str, object] = {}

    # -- which question ----------------------------------------------------------------

    def question_for(self, a) -> object:
        if a.name in self.questions:
            return self.questions[a.name]
        ids = a.gradescope
        questions = self.gs.questions(self.course, ids['pdf'])
        wanted = ids.get('question')
        if wanted is not None:
            match = [q for q in questions if str(q.id) == str(wanted) or q.title == str(wanted)]
        else:
            top = max((q.weight for q in questions), default=None)
            match = [q for q in questions if q.weight == top]
        if len(match) != 1:
            listing = ", ".join(f"{q.title!r} ({q.weight:g} pts, id {q.id})" for q in questions)
            how = f"named {wanted!r}" if wanted is not None else "that is the largest"
            raise GradescopeError(f"{a.name}: no single question {how} on PDF assignment {ids['pdf']}. "
                                  f"Questions: {listing}. Set gradescope.question.")
        self.questions[a.name] = match[0]
        return match[0]

    # -- mapping students to question submissions ----------------------------------------

    def qsid_for(self, a, asid: str) -> Optional[int]:
        state_path = a.export_dir / STATE
        state = _load(state_path)
        question = self.question_for(a)
        cache = state.setdefault('qsid_by_asid', {}).setdefault(str(question.id), {})
        if asid in cache:
            return cache[asid]
        known = set(cache.values())
        for qsid in self.gs.question_submission_ids(self.course, question.id):
            if qsid in known:
                continue
            page = self.gs.grading_page(self.course, question.id, qsid)
            cache[str(page['submission']['assignment_submission_id'])] = qsid
        _save(state_path, state)
        return cache.get(asid)

    # -- entering ------------------------------------------------------------------------

    def enter(self, a, st: dict, pdf_rows: List[Dict[str, str]], final_score: Optional[float],
              zero: bool = False, dry_run: bool = False, max_score: Optional[float] = None) -> Entry:
        key = student_key(st.get('sid'), st.get('email'))
        row = next((r for r in pdf_rows if row_key(r) == key and submitted(r)), None)
        if row is None:
            return Entry("no-pdf", "no PDF submission on Gradescope to grade")
        question = self.question_for(a)
        if not zero:
            if final_score is None:
                return Entry("no-score", "no final score recorded for the report")
            if not max_score:
                max_score = rubric_total(a)
        asid = row['Submission ID']
        qsid = self.qsid_for(a, asid)
        if qsid is None:
            return Entry("error", f"could not find submission {asid} on question {question.title!r}")
        page = self.gs.grading_page(self.course, question.id, qsid)

        weight = float(page['question']['weight'])
        # Negative scoring starts at full marks: enter the points off. Positive scoring
        # starts at zero: enter the score itself. Either way Gradescope shows the same.
        negative = page['question'].get('scoring_type') == 'negative'
        items = page.get('rubric_items') or []
        rubric = {int(i['id']): False for i in items}
        if zero:
            # A rubric item taking off the whole question. Gradescope stores deductions on a
            # negatively scored question as positive weights ("No sketch: 6.0"), so match the size
            wipe = next((i for i in items if abs(abs(float(i['weight'])) - weight) < 1e-6), None)
            if wipe and negative:
                rubric[int(wipe['id'])] = True
                points = 0.0
            else:
                points = -weight if negative else 0.0
            target = 0.0
        else:
            # Points off as on the report: LaTA's total minus the final score (late penalty
            # included), whatever the question itself is worth
            off = round(float(final_score) - float(max_score), 2)
            target = weight + off
            points = off if negative else round(target, 2)
            if negative and abs(off) < 1e-9:
                full = [i for i in items if abs(float(i['weight'])) < 1e-9]
                full.sort(key=lambda i: 0 if any(w in i['description'].lower() for w in ('correct', 'full')) else 1)
                if full:
                    rubric[int(full[0]['id'])] = True
                points = 0.0
                target = weight
        expected = target
        if page['question'].get('floor'):
            expected = max(expected, 0.0)
        if page['question'].get('ceiling'):
            expected = min(expected, weight)

        record = {"asid": asid, "qsid": qsid, "points": points,
                  "rubric": sorted(k for k, v in rubric.items() if v), "expected_score": expected,
                  "kind": "zero" if zero else "score"}
        current = float(page['submission'].get('score') or 0.0)
        graded = bool(page['submission'].get('graded'))
        prev = st.get('gradescope') or {}
        # LaTA's own entry, still as it left it (a "match" was someone else's grade)
        ours = prev.get('status') == 'entered' and prev.get('qsid') == qsid and \
            abs(float(prev.get('expected_score', -1e9)) - current) < 1e-6
        if graded and abs(current - expected) < 1e-6:
            return Entry("match", f"already {current:g} on Gradescope", record)
        if graded and not ours:
            return Entry("conflict", f"already graded on Gradescope as {current:g}; LaTA has {expected:g}. Left alone.")
        if dry_run:
            return Entry("would-enter", f"would enter {expected:g} (adjustment {points:g})", record)

        self.gs.save_grade(self.course, question.id, qsid, rubric, points)
        check = self.gs.grading_page(self.course, question.id, qsid)
        got = float(check['submission'].get('score') or 0.0)
        if abs(got - expected) > 1e-6:
            return Entry("error", f"saved, but Gradescope shows {got:g} instead of {expected:g}", record)
        record["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return Entry("entered", f"entered {expected:g} on Gradescope", record)

    def published(self, a, st_with_qsid: Optional[dict]) -> Optional[bool]:
        """Whether the PDF assignment's grades are published (None if it cannot be told)."""
        if not st_with_qsid:
            return None
        question = self.question_for(a)
        page = self.gs.grading_page(self.course, question.id, st_with_qsid['qsid'])
        return bool(page['assignment'].get('published'))


def rubric_total(a) -> float:
    """Points LaTA's rubric is out of (extra credit excluded), as in the reports."""
    path = Path(a.config['paths']['rubrics_dir']) / a.config['paths']['rubric_file']
    rubric = yaml.safe_load(path.read_text()) or {}
    return float(sum(item.get('points', 0) for sections in rubric.values() for section in sections
                     for item in section.get('items', []) if not item.get('is_extra_credit', False)))


_FINAL = re.compile(r"\\textbf\{Final Score:\}\s*&\s*\\textbf\{(-?[\d.]+)\}")
_TOTAL = re.compile(r"\\textbf\{TOTAL\}\s*&\s*\\textbf\{(-?[\d.]+)\}\s*&\s*(-?[\d.]+)")


def score_from_report(report: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """(final score, max score) as printed on a feedback report, read from its LaTeX
    source (kept in the output folder's logs/ after compiling). The final score is the
    late-penalty line when there is one, otherwise the TOTAL row."""
    if not report:
        return None, None
    pdf = Path(report)
    for tex in (pdf.parent / "logs" / f"{pdf.stem}.tex", pdf.with_suffix(".tex")):
        if tex.exists():
            text = tex.read_text(errors="replace")
            total = _TOTAL.search(text)
            final = _FINAL.search(text)
            if total:
                return float(final.group(1) if final else total.group(1)), float(total.group(2))
    return None, None


def final_score_for(a, name: str, sid: str, charged_at=None,
                    report: Optional[str] = None) -> Tuple[Optional[float], Optional[float], str, bool]:
    """(final score, max score, note, authoritative) for a student's feedback report.

    In order: the final score the reporter recorded in grading_metadata.yaml (every report
    since that was added), the score printed on the report itself, and only then a rebuild
    from the per-problem results. A rebuild depends on today's late-penalty settings, which
    may differ from those the report used, so it is not authoritative: it may fill in a
    missing grade, but never revise one already entered. (None, None, why, False) when the
    score cannot be established.
    """
    from lata.reporter import late_days_and_fraction
    out = Path(f"data/output/{a.name}_Feedback")
    meta_path = out / "grading_metadata.yaml"
    meta = (yaml.safe_load(meta_path.read_text()) or {}) if meta_path.exists() else {}
    safe = name.replace(" ", "_").replace("(", "").replace(")", "")
    m = meta.get(safe) or {}
    if 'final_score' in m:
        return float(m['final_score']), float(m.get('max_score') or 0) or None, "", True
    final, max_score = score_from_report(report)
    if final is not None:
        return final, max_score, "", True
    if a.corrections:
        return None, None, "corrections report without a recorded final score", False
    debug = out / "debug" / f"{name} ({sid})"
    results = sorted(debug.glob("*/result.json")) if debug.exists() else []
    if not results:
        return None, None, "no report source or saved results to read the score from", False
    total = max_score = 0.0
    for path in results:
        for item in json.loads(path.read_text()).get("items", []):
            total += float(item.get("score", 0))
            if not item.get("is_extra_credit"):
                max_score += float(item.get("max_score", 0))
    late = a.config.get('grading', {}).get('late_penalties', {})
    elsewhere = float(late.get('points_awarded_elsewhere', 0) or 0)
    note = "rebuilt from saved results (report source not found)"
    if 'late_penalty' in m:
        pct = float(m.get('late_penalty') or 0.0)
    elif not late.get('enabled') or not late.get('due_date'):
        pct = 0.0
    else:
        when = parse_ts(m.get('submission_created_at') if m.get('submission_created_at') not in (None, 'Unknown')
                        else charged_at)
        if when is None:
            return None, None, "late penalty not recorded and submission time unknown", False
        from lata.ingest import load_extensions
        extensions = load_extensions(a.config['paths']['export_dir'])
        days, pct = late_days_and_fraction(gradescope_ts(when), late['due_date'],
                                           float(late.get('penalty_per_day', 0.2)),
                                           float(extensions.get(name, 0) or 0))
        note += f"; late penalty not recorded, recalculated as {pct:.0%} ({days} day(s) late)"
    return round(total - pct * (max_score + elsewhere), 4), max_score, note, False
