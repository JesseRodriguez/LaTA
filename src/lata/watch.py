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
Watch a list of assignments and grade submissions as they arrive, until stopped.

List the assignments in config.yaml and run `uv run grade.py`:

    assignments:
      - name: ME461_HW1
        rubric_file: ME461_HW1_rubric.yaml
        solution_file: data/solutions/ME461-HW1-SOLUTION.tex
        due_date: "2026-10-09 06:59:59"          # GMT, like grading.late_penalties.due_date
      - name: ME461_HW1_CORRECTIONS
        rubric_file: ME461_HW1_CORRECTIONS_rubric.yaml
        solution_file: data/solutions/ME461-HW1-SOLUTION.tex
        due_date: "2026-10-16 06:59:59"

Every few minutes the watcher refreshes each assignment's exports, works out from the
ledger (lata.ledger) what each student needs -- grading, a missing-file notice, a flag for
you -- and does it. A report with a grading failure, a segmentation warning or a possible
prompt injection is held for you. Anything that needs you is emailed to
watch.notify_email.

Exports come from a source. `inbox` (the default) unpacks Gradescope .zip exports dropped
in data/input/inbox/<assignment name>/ -- so re-exporting is all a late submission needs.
A corrections assignment shares the first pass's PDF assignment: drop that export in the
first pass's inbox and both use it. `gradescope` fetches them itself and enters the grades
on Gradescope too (lata.gradescope_sync).

Feedback waits for you by default: grade any hand-graded questions, publish on Gradescope,
then type `send` into the running watcher. It checks the grades are published and complete,
posts them to Canvas, and emails the reports. With watch.auto_send_feedback the watcher does
that itself, per student, whenever the checks pass: once you have published, each student
goes out as soon as they are fully graded on Gradescope (immediately with nothing graded by
hand), with a Canvas sync first.
"""

import copy
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import yaml

from lata.inbox import prepare_from_inbox
from lata.ingest import StudentSubmission
from lata.ledger import (Ledger, Policy, corrections_pdf_check, decide, gradescope_ts, iso,
                         parse_ts, read_snapshot, student_key, submission_key, utcnow)
from lata.pipeline import GradingOptions, grade_assignment, safe_name

DEFAULTS = {
    'poll_minutes': 10,
    'settle_minutes': 15,
    'notice_after_hours': 1,
    'auto_send_feedback': False,
    'send_notices': True,
    'notify_email': None,
    'source': 'inbox',
    'inbox_dir': 'data/input/inbox',
    'unload_model_after_idle_minutes': 120,
    'gradescope': {},
}
MAX_ATTEMPTS = 2                     # tries at producing a student's report before holding it

GRADESCOPE_DEFAULTS = {
    'course': None,
    'profile_dir': None,             # default ~/.lata/gradescope-profile
    'enter_grades': True,
    'canvas_sync': True,
}


@dataclass
class Assignment:
    name: str
    config: dict                    # full config with this assignment's paths and grading
    due: object                     # naive UTC datetime
    corrections: bool
    base: Optional[str]             # first-pass assignment name, for corrections
    watch_until: object = None
    gradescope: Optional[dict] = None   # {tex, pdf, question?} Gradescope assignment ids

    @property
    def export_dir(self) -> Path:
        return Path(self.config['paths']['export_dir'])

    @property
    def output_dir(self) -> Path:
        return Path(f"data/output/{self.name}_Feedback")


@dataclass
class Events:
    """What one pass did; anything in `attention` is emailed to the instructor."""
    info: List[str] = field(default_factory=list)
    attention: List[str] = field(default_factory=list)


def _merge(base: dict, override: dict):
    """Deep-merge override into base: `grading: {late_penalties: {points_awarded_elsewhere: 4}}`
    on one assignment changes that one value, not the whole late_penalties block."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def build_assignments(config: dict) -> List[Assignment]:
    """Turn the `assignments:` list into full per-assignment configs, and check them."""
    problems, result = [], []
    names = [a.get('name') for a in config['assignments']]
    for entry in config['assignments']:
        name = entry.get('name')
        if not name:
            problems.append("an entry under assignments: has no name")
            continue
        cfg = copy.deepcopy({k: v for k, v in config.items() if k not in ('assignments', 'watch')})
        paths = cfg.setdefault('paths', {})
        grading = cfg.setdefault('grading', {})
        paths.update(export_dir=entry.get('export_dir', f"data/input/{name}"),
                     rubric_file=entry.get('rubric_file', f"{name}_rubric.yaml"))
        paths.setdefault('rubrics_dir', 'data/rubrics')
        paths.setdefault('templates_dir', 'templates')
        if entry.get('solution_file'):
            paths['solution_file'] = entry['solution_file']
        grading['assignment_name'] = name
        if entry.get('subject'):
            grading['subject'] = entry['subject']
        late = grading.setdefault('late_penalties', {})
        if entry.get('due_date'):
            late['due_date'] = entry['due_date']
        for section in ('paths', 'grading'):                 # any other per-assignment overrides
            _merge(cfg[section], entry.get(section) or {})

        due = parse_ts(late.get('due_date'))
        corrections = name.endswith('_CORRECTIONS')
        base = name[:-len('_CORRECTIONS')] if corrections else None
        rubric = Path(paths['rubrics_dir']) / paths['rubric_file']
        if due is None:
            problems.append(f"{name}: no due_date")
        if not rubric.exists():
            problems.append(f"{name}: rubric not found at {rubric}")
        if not paths.get('solution_file') or not Path(paths['solution_file']).exists():
            problems.append(f"{name}: solution not found at {paths.get('solution_file')}")
        if corrections and base not in names and not Path(f"data/output/{base}_Feedback").exists():
            problems.append(f"{name}: its first pass {base} is neither listed nor graded")
        result.append(Assignment(name=name, config=cfg, due=due, corrections=corrections,
                                 base=base, watch_until=parse_ts(entry.get('watch_until')),
                                 gradescope=entry.get('gradescope')))
    if problems:
        raise ValueError("Cannot start watching:\n  - " + "\n  - ".join(problems))
    # First passes before their corrections, so a late first pass is graded (and its time
    # frozen) before its corrections are considered in the same pass
    return sorted(result, key=lambda a: a.corrections)


class InboxSource:
    """Gradescope .zip exports dropped in <inbox>/<assignment name>/."""

    def __init__(self, inbox_dir: str):
        self.root = Path(inbox_dir)

    def describe(self, a: Assignment) -> str:
        return str(self.root / a.name)

    def refresh(self, a: Assignment, events: Events) -> None:
        folder = self.root / a.name
        folder.mkdir(parents=True, exist_ok=True)
        result = prepare_from_inbox(folder, a.export_dir, quiet=True)
        if result:
            for action in result.actions:
                events.info.append(f"{a.name}: {action}")


class Watcher:
    def __init__(self, config: dict, config_path: str, options: GradingOptions,
                 auto_send: Optional[bool] = None, dry_run: bool = False):
        self.config = config
        self.config_path = config_path
        self.options = options
        self.dry_run = dry_run
        self.settings = {**DEFAULTS, **(config.get('watch') or {})}
        self.gs_settings = {**GRADESCOPE_DEFAULTS, **(self.settings.get('gradescope') or {})}
        if auto_send is not None:
            self.settings['auto_send_feedback'] = auto_send
        self.assignments = build_assignments(config)
        self.gs = None                     # Gradescope session, opened on first use
        self.grade_entry = None
        self.reported = set()              # attention messages already sent, to not repeat
        self.announced: Dict[str, frozenset] = {}
        if self.settings['source'] == 'inbox':
            self.source = InboxSource(self.settings['inbox_dir'])
        elif self.settings['source'] == 'gradescope':
            problems = []
            if not self.gs_settings['course']:
                problems.append("watch.gradescope.course is not set")
            for a in self.assignments:
                ids = a.gradescope or {}
                if not (ids.get('tex') and ids.get('pdf')):
                    problems.append(f"{a.name}: needs gradescope: {{tex: <id>, pdf: <id>}}")
            if problems:
                raise ValueError("Cannot start watching:\n  - " + "\n  - ".join(problems))
            self.source = None             # created with the session
        else:
            raise ValueError(f"Unknown watch.source {self.settings['source']!r} (inbox or gradescope)")
        self.ledgers: Dict[str, Ledger] = {}
        self.snapshots: Dict[str, dict] = {}
        self.emailers: Dict[str, object] = {}
        self.auth = None
        self.server = None
        self.pristine_grader = copy.deepcopy(config.get('system', {}).get('grader', {}))
        self.last_graded = None
        self.waiting: Dict[str, list] = {}
        self.log_path = Path("data/output/watch_log.txt")

    # -- email ------------------------------------------------------------------------

    @property
    def sends_email(self) -> bool:
        return not self.dry_run          # feedback is always sent eventually, by `send` if not automatically

    def authenticate(self):
        """Log in to the mail backend once, at startup, while someone is at the keyboard."""
        if not self.sends_email:
            return
        first = self.emailer(self.assignments[0])
        if first.email_backend == 'applescript':
            first.authenticate_applescript()
        else:
            first.authenticate()
        self.auth = {k: getattr(first, k) for k in
                     ('from_email', 'smtp_connection', 'username', 'password')}
        for e in self.emailers.values():
            for k, v in self.auth.items():
                setattr(e, k, v)

    def emailer(self, a: Assignment):
        if a.name not in self.emailers:
            from lata.emailer import FeedbackEmailer
            e = FeedbackEmailer(config_path=self.config_path, output_dir=str(a.output_dir),
                                is_corrections_mode=a.corrections, config=a.config)
            if self.auth:
                for k, v in self.auth.items():
                    setattr(e, k, v)
            self.emailers[a.name] = e
        e = self.emailers[a.name]
        e.ensure_connection()
        return e

    def notify_instructor(self, events: Events):
        if not events.attention:
            return
        lines = ["LaTA needs your attention:", ""] + [f"  - {m}" for m in events.attention]
        if events.info:
            lines += ["", "Also in this pass:", ""] + [f"  - {m}" for m in events.info]
        body = "\n".join(lines)
        to = self.settings['notify_email']
        if to and self.auth:
            self.emailer(self.assignments[0]).send_plain_email(
                to, f"LaTA: {len(events.attention)} item(s) need attention", body)
        elif sys.platform == "darwin" and shutil.which("osascript"):
            msg = f"{len(events.attention)} item(s) need attention -- see {self.log_path}"
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "LaTA"'],
                           capture_output=True)

    # -- model server -----------------------------------------------------------------

    def keep_model_loaded(self):
        """Start the grading server once and reuse it across passes (loading a large GGUF
        takes minutes). grade_assignment() leaves an already-resolved endpoint alone."""
        if self.server is None and self.config['system']['grader'].get('model'):
            from lata.serving import prepare_grader
            Path("data/output").mkdir(parents=True, exist_ok=True)
            self.server = prepare_grader(self.config, self.options.concurrency,
                                         log_dir=Path("data/output"))

    def unload_if_idle(self):
        idle = self.settings['unload_model_after_idle_minutes']
        if self.server is None or not idle or self.last_graded is None:
            return
        if time.monotonic() - self.last_graded > idle * 60:
            print(f"💤 No grading for {idle} min -- stopping the model server to free memory "
                  f"(it restarts on the next submission)")
            self.stop_server()

    def stop_server(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
            self.config['system']['grader'] = copy.deepcopy(self.pristine_grader)

    # -- gradescope --------------------------------------------------------------------

    @property
    def uses_gradescope(self) -> bool:
        return self.settings['source'] == 'gradescope'

    def gradescope(self):
        """The logged-in Gradescope session, opened (or reopened) on demand."""
        if self.gs is None:
            from lata.gradescope import Gradescope
            from lata.gradescope_sync import GradescopeSource, GradeEntry
            gs = Gradescope(self.gs_settings['profile_dir'])
            gs.__enter__()                                  # raises NotLoggedIn
            self.gs = gs
            course = int(self.gs_settings['course'])
            self.source = GradescopeSource(gs, course, self.settings['inbox_dir'])
            self.grade_entry = GradeEntry(gs, course)
        return self.gs

    def close_gradescope(self):
        if self.gs is not None:
            self.gs.close()
            self.gs = None

    def attention_once(self, events: Events, message: str):
        if message not in self.reported:
            self.reported.add(message)
            events.attention.append(message)

    def ledger(self, a: Assignment) -> Ledger:
        if a.name not in self.ledgers:
            ledger = Ledger(a.output_dir)
            if self.dry_run:
                ledger.save = lambda: None                   # a dry run changes nothing
            self.ledgers[a.name] = ledger
        return self.ledgers[a.name]

    def sync_grades(self, a: Assignment, ledger: Ledger, events: Events):
        """Put graded students' scores (and missing-.tex zeros) on Gradescope."""
        from lata.gradescope_sync import final_score_for
        rows = self.source.pdf_rows.get(a.name, [])
        pdf_check = corrections_pdf_check(a.base, self.pdf_metadata_for(a)) if a.corrections else None
        problems: Dict[Optional[str], List[str]] = {}
        for key, st in ledger.students.items():
            rec = st.get("gradescope") or {}
            graded = st.get("graded")
            name = st.get("name", key)
            if graded and not st.get("held") and st.get("report"):
                if rec.get("status") == "not-applied":
                    continue
                if pdf_check and pdf_check(name, st.get("sid"), st.get("email")) is False:
                    st["gradescope"] = {"kind": "score", "status": "not-applied"}
                    continue                 # incomplete corrections: grade is not entered
                charged = graded.get("effective")
                if not charged and key in self.snapshots.get(a.name, {}):
                    s = self.snapshots[a.name][key]
                    times = [x.created_at for x in (s.tex, s.pdf) if x is not None]
                    charged = max(times) if times else None
                final, max_score, note, authoritative = final_score_for(
                    a, name, st.get("sid", ""), charged, report=st.get("report"))
                if rec.get("kind") == "score" and rec.get("status") in ("entered", "match"):
                    # Already on Gradescope: revisit only if the report's own number differs
                    # from what was entered (a rebuilt number is never grounds to change it)
                    if final is None or not authoritative or abs(round(final - (max_score or 0), 2)
                                                                 - float(rec.get("lata_points", rec.get("points", 0)))) < 1e-6:
                        continue
                    note = (note + "; " if note else "") + "score differs from what was entered"
                if final is None:
                    self.attention_once(events, f"{a.name}: {name}: grade not entered ({note}); enter it by hand")
                    continue
                if note:
                    events.info.append(f"{a.name}: {name}: {note}")
                entry = self.grade_entry.enter(a, st, rows, final, max_score=max_score, dry_run=self.dry_run)
                if entry.record is not None:
                    entry.record["lata_points"] = round(final - (max_score or 0), 2)
            elif (not graded and not a.corrections and rec.get("kind") != "zero"
                  and (st.get("notices") or {}).get("missing_tex", "").startswith("20")):
                entry = self.grade_entry.enter(a, st, rows, None, zero=True, dry_run=self.dry_run)
            else:
                continue
            if entry.status in ("entered", "match"):
                st["gradescope"] = {**(entry.record or {}), "status": entry.status}
                events.info.append(f"{a.name}: {name}: {entry.message}")
            elif entry.status == "would-enter":
                events.info.append(f"{a.name}: {name}: {entry.message}")
            elif entry.status == "no-pdf":
                continue                     # tried again when their PDF shows up
            elif entry.status == "conflict":
                st["gradescope"] = {"kind": "score", "status": "conflict", "message": entry.message}
                problems.setdefault(entry.message if "already graded" not in entry.message else None, []).append(name)
            else:
                problems.setdefault(f"could not enter grade: {entry.message}", []).append(name)
        # One message per problem, not per student: a setup issue hits the whole class
        for message, names in problems.items():
            if message is None:
                continue                             # differing hand-entered grades: listed below, one each
            who = names[0] if len(names) == 1 else f"{len(names)} students ({', '.join(names[:3])}{', ...' if len(names) > 3 else ''})"
            self.attention_once(events, f"{a.name}: {who}: {message}")
        for key, st in ledger.students.items():
            rec = st.get("gradescope") or {}
            if rec.get("status") == "conflict" and "already graded" in rec.get("message", ""):
                self.attention_once(events, f"{a.name}: {st.get('name', key)}: {rec['message']}")

    # -- release -----------------------------------------------------------------------

    def awaiting_release(self, a: Assignment, ledger: Ledger) -> List[tuple]:
        """Graded students whose feedback is ready to go once you release it."""
        out = []
        for key, st in ledger.students.items():
            if (not st.get("graded") or st.get("held") or st.get("feedback_sent_at")
                    or not st.get("report") or not Path(st["report"]).exists()):
                continue
            if self.uses_gradescope and self.gs_settings['enter_grades'] and \
                    (st.get("gradescope") or {}).get("status") not in ("entered", "match", "not-applied"):
                continue                     # not on Gradescope yet (or a conflict to resolve)
            out.append((key, st))
        return out

    def announce_release(self, a: Assignment, ledger: Ledger, events: Events):
        ready = self.awaiting_release(a, ledger)
        keys = frozenset(k for k, _ in ready)
        if not keys or keys == self.announced.get(a.name):
            return
        self.announced[a.name] = keys
        steps = ("grade any hand-graded questions and publish the grades on Gradescope, then "
                 if self.uses_gradescope else "")
        events.attention.append(f"{a.name}: feedback for {len(keys)} student(s) is ready. "
                                f"To release it, {steps}type `send {a.name}` in the watcher.")

    def release(self, name: Optional[str], force: bool = False):
        targets = [a for a in self.assignments if name is None or a.name.lower() == name.lower()]
        if not targets:
            print(f"   No assignment called {name!r}. Watching: {', '.join(a.name for a in self.assignments)}")
            return
        events = Events()
        for a in targets:
            ledger = self.ledger(a)
            ready = self.awaiting_release(a, ledger)
            if not ready:
                if name is not None:
                    print(f"   {a.name}: nothing waiting to be sent")
                continue
            allowed = self.release_checks(a, ready, force, events) if self.uses_gradescope else {k for k, _ in ready}
            if not allowed:
                continue
            self.send_feedback(a, ledger, utcnow(), events, only=allowed)
            ledger.save()
        if events.info or events.attention:
            self.finish_pass(events)

    def auto_release(self, a: Assignment, ledger: Ledger, now, events: Events):
        """auto_send_feedback: do what `send` would, whenever its checks pass.

        Students go out once the assignment is published on Gradescope and they are fully
        graded there -- straight away with nothing graded by hand, otherwise as soon as
        their hand-graded questions are done -- with a Canvas sync first. Students graded
        before the watcher started are never sent automatically."""
        ready = [(k, st) for k, st in self.awaiting_release(a, ledger)
                 if not (st.get("graded") or {}).get("seeded")]
        if not ready:
            return
        if not self.uses_gradescope:
            self.send_feedback(a, ledger, now, events, only={k for k, _ in ready})
            return
        allowed = self.release_checks(a, ready, False, events, auto=True)
        if allowed:
            self.send_feedback(a, ledger, now, events, only=allowed)

    def release_checks(self, a: Assignment, ready: List[tuple], force: bool, events: Events,
                       auto: bool = False) -> set:
        """Before sending: grades published and complete on Gradescope, then post to Canvas.

        Returns the students who may be sent. By hand (`send`), all of them or none: anyone
        not fully graded stops the release (unless --force). Automatically, those who are
        fully graded go now and the rest wait."""
        say = (lambda m: self.attention_once(events, m)) if auto else print
        try:
            self.gradescope()
            course, pdf = int(self.gs_settings['course']), a.gradescope['pdf']
            with_qsid = next((st["gradescope"] for _, st in ready if (st.get("gradescope") or {}).get("qsid")), None)
            if with_qsid is None:
                with_qsid = next((st["gradescope"] for st in self.ledger(a).students.values()
                                  if (st.get("gradescope") or {}).get("qsid")), None)
            published = self.grade_entry.published(a, with_qsid)
            if published is False:
                say(f"{a.name}: feedback is ready and goes out automatically once you publish the grades on Gradescope."
                    if auto else f"   ❌ {a.name}: grades are not published on Gradescope yet. "
                                 f"Publish them, then `send {a.name}` again.")
                return set()
            rows = {row_key: r for r in self.gs.scores(course, pdf) for row_key in [student_key(r.get('SID'), r.get('Email'))]}
            complete = {key for key, st in ready if (rows.get(key) or {}).get('Status') == 'Graded'}
            incomplete = [st.get("name") for key, st in ready if key not in complete]
            if auto:
                allowed = complete           # the rest wait for their hand-graded questions
                if not allowed:
                    return set()
            elif incomplete and not force:
                print(f"   ❌ {a.name}: {len(incomplete)} student(s) are not fully graded on Gradescope "
                      f"(hand-graded questions?): {', '.join(incomplete)}")
                print(f"      Finish them, or `send {a.name} --force` to send anyway.")
                return set()
            else:
                allowed = {k for k, _ in ready}
            if self.gs_settings['canvas_sync']:
                if self.dry_run:
                    print(f"   [DRY-RUN] would post {a.name}'s grades to Canvas")
                else:
                    print(f"   ⏳ Posting {a.name} grades to Canvas...")
                    self.gs.post_grades_to_canvas(course, pdf)
                    events.info.append(f"{a.name}: grades posted to Canvas")
            return allowed
        except Exception as e:
            if not auto:
                print(f"   ❌ {a.name}: {e}")
            self.attention_once(events, f"{a.name}: release stopped: {e}")
            return set()

    # -- one pass ---------------------------------------------------------------------

    def first_pass_lookup(self, a: Assignment):
        """How each student stands on the first pass of corrections assignment `a`.

        From the first pass's ledger when it is watched too; otherwise (graded earlier with
        plain grade.py) from its reports and grading_metadata.yaml."""
        base_dir = Path(f"data/output/{a.base}_Feedback")
        base_ledger = self.ledgers.get(a.base) or Ledger(base_dir)
        base_snapshot = self.snapshots.get(a.base)
        if base_snapshot is None:
            base_export = Path(f"data/input/{a.base}")
            base_snapshot = read_snapshot(base_export) if base_export.exists() else {}
        meta_path = base_dir / "grading_metadata.yaml"
        meta = (yaml.safe_load(meta_path.read_text()) or {}) if meta_path.exists() else {}
        snapshot = self.snapshots.get(a.name, {})

        def lookup(key):
            status, frozen = base_ledger.first_pass(key)
            if status == "graded":
                return status, frozen
            name = safe_name(snapshot[key].name) if key in snapshot else None
            if name and ((base_dir / f"Feedback_{name}.pdf").exists() or name in meta):
                return "graded", parse_ts((meta.get(name) or {}).get('submission_created_at'))
            b = base_snapshot.get(key)
            return ("pending", None) if (b and b.tex and b.tex.has_tex) else ("none", None)
        return lookup

    def pdf_metadata_for(self, a: Assignment) -> Path:
        own = a.export_dir / "pdf_submission_metadata.yml"
        if a.corrections and not own.exists():
            base = next((b for b in self.assignments if b.name == a.base), None)
            base_dir = base.export_dir if base else Path(f"data/input/{a.base}")
            return base_dir / "pdf_submission_metadata.yml"
        return own

    def run_pass(self) -> bool:
        """Refresh, decide and act for every assignment. Returns False to stop watching."""
        events = Events()
        now = utcnow()
        if self.uses_gradescope:
            try:
                self.gradescope()
                # Session works again: a later failure should be reported afresh
                self.reported = {m for m in self.reported if not m.startswith("Gradescope:")}
            except Exception as e:
                self.close_gradescope()
                self.attention_once(events, f"Gradescope: {e}. Nothing is fetched or entered until this is fixed.")
                self.finish_pass(events)
                return True
        for a in self.assignments:
            if a.watch_until and now > a.watch_until:
                continue
            try:
                self.source.refresh(a, events)
            except Exception as e:
                self.attention_once(events, f"{a.name}: could not refresh exports: {e}")
                if self.uses_gradescope:
                    self.close_gradescope()       # reopened (and re-checked) next pass
                continue
            keep_going = self.process(a, now, events)
            if not keep_going:
                self.finish_pass(events)
                return False
        if self.gs is not None:
            try:
                self.gs.save()           # keep the saved session as fresh as Gradescope's
            except Exception:
                pass
        self.finish_pass(events)
        return True

    def process(self, a: Assignment, now, events: Events) -> bool:
        if not (a.export_dir / "submission_metadata.yml").exists() and \
                not self.pdf_metadata_for(a).exists():
            return True                                      # nothing exported yet
        snapshot = read_snapshot(a.export_dir, self.pdf_metadata_for(a))
        ledger = self.ledger(a)
        self.snapshots[a.name] = snapshot
        ledger.observe(snapshot, now)
        ledger.seed_from_outputs(snapshot, a.output_dir, safe_name)

        extensions = {}
        ext_path = a.export_dir / "extensions.yaml"
        if ext_path.exists():
            with open(ext_path) as f:
                extensions = {k: float(v) for k, v in (yaml.safe_load(f) or {}).items()}
        policy = Policy(due=a.due, settle=timedelta(minutes=self.settings['settle_minutes']),
                        notice_after=timedelta(hours=self.settings['notice_after_hours']),
                        extensions=extensions, corrections=a.corrections,
                        first_pass=self.first_pass_lookup(a) if a.corrections else None)
        actions, waiting = decide(ledger, snapshot, policy, now)
        self.waiting[a.name] = waiting

        for act in [x for x in actions if x.kind == "flag"]:
            ledger.get(act.key).setdefault("flags", {})[act.flag_id] = {"at": iso(now), "message": act.detail}
            events.attention.append(f"{a.name}: {act.name} {act.detail}")

        for act in [x for x in actions if x.kind == "notice"]:
            student = {'name': act.name, 'email': snapshot[act.key].email, 'reason': act.detail}
            if self.dry_run:
                events.info.append(f"{a.name}: [DRY-RUN] would send {act.detail} notice to {act.name}")
                continue
            if not self.settings['send_notices']:
                events.info.append(f"{a.name}: {act.name} needs a {act.detail} notice (notices are off)")
                ledger.get(act.key).setdefault("notices", {})[act.detail] = f"not sent (off) {iso(now)}"
                continue
            if self.emailer(a).send_notice(student, act.detail):
                ledger.get(act.key).setdefault("notices", {})[act.detail] = iso(now)
                events.info.append(f"{a.name}: {act.detail} notice sent to {act.name}")
            else:
                events.attention.append(f"{a.name}: could not send {act.detail} notice to {act.name}")
        ledger.save()

        grade = {x.key: x for x in actions if x.kind == "grade"}
        if grade and self.dry_run:
            names = ", ".join(sorted(x.name for x in grade.values()))
            events.info.append(f"{a.name}: [DRY-RUN] would grade {len(grade)}: {names}")
        elif grade:
            if not self.grade(a, grade, snapshot, ledger, now, events):
                ledger.save()
                return False

        if self.uses_gradescope and self.gs_settings['enter_grades']:
            try:
                self.sync_grades(a, ledger, events)
            except Exception as e:
                self.attention_once(events, f"{a.name}: entering grades on Gradescope failed: {e}")
            ledger.save()

        if self.dry_run:
            ready = self.awaiting_release(a, ledger)
            if ready:
                events.info.append(f"{a.name}: [DRY-RUN] feedback for {len(ready)} student(s) would then wait for `send`")
        elif self.settings['auto_send_feedback']:
            self.auto_release(a, ledger, now, events)
        else:
            self.announce_release(a, ledger, events)
        ledger.save()
        return True

    def grade(self, a: Assignment, grade: dict, snapshot: dict, ledger: Ledger, now,
              events: Events) -> bool:
        print(f"\n🗂️  {a.name}: grading {len(grade)} student(s)")
        try:
            self.keep_model_loaded()
        except RuntimeError as e:
            events.attention.append(f"{a.name}: could not start the grading model: {e}")
            return True
        cfg = copy.deepcopy(a.config)
        cfg['system']['grader'] = copy.deepcopy(self.config['system']['grader'])

        def key_of(s: StudentSubmission) -> str:
            return submission_key(s.student_sid, s.student_email)

        def charged_time(s: StudentSubmission):
            act = grade.get(key_of(s))
            return gradescope_ts(act.created_at) if act else None

        result = grade_assignment(cfg, self.config_path, self.options,
                                  select=lambda s: key_of(s) in grade, created_at=charged_time)
        self.last_graded = time.monotonic()
        if result.error:
            events.attention.append(f"{a.name}: grading did not run: {result.error}")
        for outcome in result.outcomes.values():
            key = submission_key(outcome.sid, outcome.email)
            act = grade.get(key)
            if act is None:
                continue
            hold = outcome.hold_reasons()
            if outcome.report is None:
                st = ledger.get(key)
                if result.interrupted:
                    continue          # stopped before its report: graded again next time
                # No report (e.g. the model server died): try once more before holding
                st["attempts"] = st.get("attempts", 0) + 1
                st["last_failure"] = "; ".join(hold)
                if st["attempts"] < MAX_ATTEMPTS:
                    events.info.append(f"{a.name}: {outcome.name}: no report ({st['last_failure']}); will try again")
                    continue
                hold = hold + [f"no report after {st['attempts']} attempts"]
            ledger.mark_graded(key, snapshot[key], act.created_at, act.pdf, now, outcome.report, hold)
            if hold:
                events.attention.append(f"{a.name}: {outcome.name} graded but held: {'; '.join(hold)}")
            else:
                events.info.append(f"{a.name}: {outcome.name} graded"
                                   + (f" ({outcome.validation_fixes} score fix(es))" if outcome.validation_fixes else ""))
        return not result.interrupted

    def send_feedback(self, a: Assignment, ledger: Ledger, now, events: Events, only=None):
        """Email reports. Automatically (only=None) never includes students graded before
        the watcher; a release by hand (`send`) passes exactly who to send to."""
        emailer = self.emailer(a)
        if a.corrections:
            # Whether each student resubmitted their PDF, from the latest exports
            emailer.corrections_pdf_check = corrections_pdf_check(a.base, self.pdf_metadata_for(a))
        for key, st in ledger.students.items():
            graded = st.get("graded")
            if only is not None and key not in only:
                continue
            if (not graded or (only is None and graded.get("seeded")) or st.get("held")
                    or st.get("feedback_sent_at") or not st.get("report") or not Path(st["report"]).exists()):
                continue
            student = SimpleNamespace(student_name=st.get("name"), student_email=st.get("email"),
                                      student_sid=st.get("sid", ""))
            if emailer.send_single_feedback(student, Path(st["report"])):
                st["feedback_sent_at"] = iso(now)
                incomplete = emailer.missing_corrections_pdf(student)
                if incomplete:
                    st["corrections_not_applied"] = True
                events.info.append(f"{a.name}: feedback sent to {st.get('name')}"
                                   + (" (no new PDF: told corrections grade is not applied)" if incomplete else ""))
            else:
                events.attention.append(f"{a.name}: could not send feedback to {st.get('name')}")

    def finish_pass(self, events: Events):
        stamp = utcnow().strftime("%Y-%m-%d %H:%M UTC")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            for m in events.attention:
                f.write(f"{stamp}  ATTENTION  {m}\n")
            for m in events.info:
                f.write(f"{stamp}  {m}\n")
        for m in events.info:
            print(f"   {m}")
        for m in events.attention:
            print(f"   ⚠️  {m}")
        self.notify_instructor(events)
        self.print_status()

    def print_status(self):
        print(f"\n🕒 {utcnow():%Y-%m-%d %H:%M} UTC")
        for a in self.assignments:
            ledger = self.ledgers.get(a.name)
            if ledger is None:
                print(f"   {a.name}: no exports yet ({self.source.describe(a)})")
                continue
            sts = ledger.students.values()
            graded = sum(1 for s in sts if s.get("graded"))
            sent = sum(1 for s in sts if s.get("feedback_sent_at"))
            held = sum(1 for s in sts if s.get("held"))
            waiting = self.waiting.get(a.name, [])
            reasons = {}
            for w in waiting:
                r = w.reason.split(";")[0]
                reasons[r] = reasons.get(r, 0) + 1
            detail = ", ".join(f"{n} {r}" for r, n in sorted(reasons.items()))
            ready = len(self.awaiting_release(a, ledger))
            on_gs = sum(1 for s in sts if (s.get("gradescope") or {}).get("status") in ("entered", "match"))
            print(f"   {a.name}: {graded} graded"
                  + (f", {on_gs} on Gradescope" if self.uses_gradescope else "")
                  + f", {sent} sent, {held} held"
                  + (f", {ready} ready to send" if ready else "")
                  + (f" | waiting: {detail}" if detail else ""))


class Console:
    """Reads commands typed into the running watcher, without stopping its checks."""

    HELP = ("   Commands: send [assignment] [--force] | status | check (check now) | help\n"
            "   (Ctrl-C stops watching)")

    def __init__(self):
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.interactive = bool(sys.stdin and sys.stdin.isatty())
        if self.interactive:
            threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in sys.stdin:
            self.queue.put(line.strip())

    def wait(self, seconds: float) -> Optional[str]:
        try:
            return self.queue.get(timeout=max(0.0, seconds))
        except queue.Empty:
            return None


def handle_command(watcher: Watcher, line: str) -> bool:
    """Run one typed command. Returns True to check for submissions right away."""
    parts = line.split()
    if not parts:
        return False
    cmd, args = parts[0].lower(), parts[1:]
    if cmd == "send":
        force = "--force" in args
        names = [x for x in args if x != "--force"]
        watcher.release(names[0] if names else None, force=force)
    elif cmd == "status":
        watcher.print_status()
    elif cmd in ("check", "now"):
        return True
    else:
        print(Console.HELP)
    return False


def run_watch(config: dict, config_path: str, options: GradingOptions, once: bool = False,
              auto_send: Optional[bool] = None, dry_run: bool = False):
    try:
        watcher = Watcher(config, config_path, options, auto_send=auto_send, dry_run=dry_run)
    except ValueError as e:
        print(f"❌ {e}")
        return
    s = watcher.settings
    once = once or dry_run
    print("👀 Watching " + ", ".join(a.name for a in watcher.assignments)
          + ("  [DRY RUN: nothing is sent, entered or saved]" if dry_run else ""))
    if watcher.uses_gradescope:
        gs = watcher.gs_settings
        print(f"   Exports from: Gradescope course {gs['course']}"
              f" | grades entered on Gradescope: {'yes' if gs['enter_grades'] else 'no'}"
              f" | Canvas sync on release: {'yes' if gs['canvas_sync'] else 'no'}")
    else:
        print(f"   Exports from: {s['source']} ({s['inbox_dir']}/<assignment>/)")
    print(f"   Feedback: {('released automatically once published and fully graded on Gradescope' if watcher.uses_gradescope else 'emailed automatically when clean') if s['auto_send_feedback'] else 'waits for you to type `send`'}"
          f" | missing-file notices: {'on' if s['send_notices'] else 'off'}"
          f" | attention emails: {s['notify_email'] or 'off (desktop notification)'}")
    try:
        watcher.authenticate()
    except Exception as e:
        print(f"❌ Email setup failed ({e}); not watching.")
        return
    console = Console()
    if not once:
        print(f"   Checking every {s['poll_minutes']} min.")
        print(Console.HELP if console.interactive else
              "   (Not a terminal: type commands is unavailable; feedback waits for a run with a terminal)")
    try:
        while True:
            if not watcher.run_pass() or once:
                break
            watcher.unload_if_idle()
            print(f"   Next check in {s['poll_minutes']} min. Type `help` for commands.")
            deadline = time.monotonic() + s['poll_minutes'] * 60
            while (left := deadline - time.monotonic()) > 0:
                line = console.wait(left)
                if line is None:
                    break
                if handle_command(watcher, line):
                    break
    except KeyboardInterrupt:
        print("\n⏹️  Stopped watching.")
    finally:
        watcher.stop_server()
        watcher.close_gradescope()
