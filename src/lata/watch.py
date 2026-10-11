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
you -- and does it. Graded reports go out by email when clean and auto-sending is on; a
report with a grading failure, a segmentation warning or a possible prompt injection is
held for you instead. Anything that needs you is emailed to watch.notify_email.

Exports come from a source. `inbox` (the default) unpacks Gradescope .zip exports dropped
in data/input/inbox/<assignment name>/ -- so re-exporting is all a late submission needs.
A corrections assignment shares the first pass's PDF assignment: drop that export in the
first pass's inbox and both use it.
"""

import copy
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import yaml

from lata.inbox import prepare_from_inbox
from lata.ingest import StudentSubmission
from lata.ledger import (Ledger, Policy, decide, gradescope_ts, iso, parse_ts, read_snapshot,
                         student_key, utcnow)
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
}


def submission_key(sid, email) -> str:
    """student_key() for a StudentSubmission, whose missing SID reads 'None' or 'Unknown'."""
    return student_key("" if str(sid) in ("None", "Unknown") else sid, email)


@dataclass
class Assignment:
    name: str
    config: dict                    # full config with this assignment's paths and grading
    due: object                     # naive UTC datetime
    corrections: bool
    base: Optional[str]             # first-pass assignment name, for corrections
    watch_until: object = None

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
            cfg[section].update(entry.get(section) or {})

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
                                 base=base, watch_until=parse_ts(entry.get('watch_until'))))
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
                 auto_send: Optional[bool] = None):
        self.config = config
        self.config_path = config_path
        self.options = options
        self.settings = {**DEFAULTS, **(config.get('watch') or {})}
        if auto_send is not None:
            self.settings['auto_send_feedback'] = auto_send
        self.assignments = build_assignments(config)
        if self.settings['source'] != 'inbox':
            raise ValueError(f"Unknown watch.source {self.settings['source']!r} (available: inbox)")
        self.source = InboxSource(self.settings['inbox_dir'])
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
        s = self.settings
        return bool(s['auto_send_feedback'] or s['send_notices'] or s['notify_email'])

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
        for a in self.assignments:
            if a.watch_until and now > a.watch_until:
                continue
            try:
                self.source.refresh(a, events)
            except Exception as e:
                events.attention.append(f"{a.name}: could not refresh exports: {e}")
                continue
            keep_going = self.process(a, now, events)
            if not keep_going:
                self.finish_pass(events)
                return False
        self.finish_pass(events)
        return True

    def process(self, a: Assignment, now, events: Events) -> bool:
        if not (a.export_dir / "submission_metadata.yml").exists() and \
                not self.pdf_metadata_for(a).exists():
            return True                                      # nothing exported yet
        snapshot = read_snapshot(a.export_dir, self.pdf_metadata_for(a))
        ledger = self.ledgers.setdefault(a.name, Ledger(a.output_dir))
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
        if grade:
            if not self.grade(a, grade, snapshot, ledger, now, events):
                ledger.save()
                return False

        if self.settings['auto_send_feedback']:
            self.send_feedback(a, ledger, now, events)
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
            ledger.mark_graded(key, snapshot[key], act.created_at, act.pdf, now, outcome.report, hold)
            if hold:
                events.attention.append(f"{a.name}: {outcome.name} graded but held: {'; '.join(hold)}")
            else:
                events.info.append(f"{a.name}: {outcome.name} graded"
                                   + (f" ({outcome.validation_fixes} score fix(es))" if outcome.validation_fixes else ""))
        return not result.interrupted

    def send_feedback(self, a: Assignment, ledger: Ledger, now, events: Events):
        for key, st in ledger.students.items():
            graded = st.get("graded")
            if (not graded or graded.get("seeded") or st.get("held") or st.get("feedback_sent_at")
                    or not st.get("report") or not Path(st["report"]).exists()):
                continue
            student = SimpleNamespace(student_name=st.get("name"), student_email=st.get("email"))
            if self.emailer(a).send_single_feedback(student, Path(st["report"])):
                st["feedback_sent_at"] = iso(now)
                events.info.append(f"{a.name}: feedback sent to {st.get('name')}")
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
            print(f"   {a.name}: {graded} graded, {sent} sent, {held} held"
                  + (f" | waiting: {detail}" if detail else ""))


def run_watch(config: dict, config_path: str, options: GradingOptions, once: bool = False,
              auto_send: Optional[bool] = None):
    try:
        watcher = Watcher(config, config_path, options, auto_send=auto_send)
    except ValueError as e:
        print(f"❌ {e}")
        return
    s = watcher.settings
    print("👀 Watching " + ", ".join(a.name for a in watcher.assignments))
    print(f"   Exports from: {s['source']} ({s['inbox_dir']}/<assignment>/)")
    print(f"   Feedback: {'emailed automatically when clean' if s['auto_send_feedback'] else 'left for post_grading.py send-feedback'}"
          f" | missing-file notices: {'on' if s['send_notices'] else 'off'}"
          f" | attention emails: {s['notify_email'] or 'off (desktop notification)'}")
    if not once:
        print(f"   Checking every {s['poll_minutes']} min. Ctrl-C to stop.")
    try:
        watcher.authenticate()
    except Exception as e:
        print(f"❌ Email setup failed ({e}); not watching. Fix it, or turn off "
              f"watch.auto_send_feedback, watch.send_notices and watch.notify_email.")
        return
    try:
        while True:
            if not watcher.run_pass() or once:
                break
            watcher.unload_if_idle()
            print(f"   Next check in {s['poll_minutes']} min (Ctrl-C to stop)")
            time.sleep(s['poll_minutes'] * 60)
    except KeyboardInterrupt:
        print("\n⏹️  Stopped watching.")
    finally:
        watcher.stop_server()
