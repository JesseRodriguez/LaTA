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
What the watcher knows about each student, and the rules for what to do next.

A Gradescope export is a snapshot: it shows each student's *current* submission and
nothing else. Grading from snapshots alone gets two things wrong:

  * Corrections are submitted to the same PDF assignment as the first pass, so once they
    open, the export shows the corrections PDF's time. Taking "the later of the .tex and
    .pdf times" from it would charge a late penalty for a correction.
  * A new PDF after corrections open might be a correction -- or a late first pass from a
    student who was never graded. The snapshot cannot say which.

The ledger remembers what the snapshots cannot: when each student was graded and from
which submission times (frozen at that moment), which notices went out, and what was
flagged. decide() is a pure function of ledger + snapshot + clock, so every timeline can
be tested without Gradescope, a model or a mail server.

Rules, per student, for an assignment (not yet graded unless stated):
  .tex and PDF        grade once past the assignment's due date and quiet for `settle` --
                      students often resubmit several times in a few minutes. Extensions do
                      not delay grading; they count for late penalties and for notices
  .tex, no PDF        notice "PDF missing, late penalty accruing" once both the deadline
                      and an hour after the .tex have passed; grade when the PDF arrives
  PDF, no .tex        notice ".tex missing" on the same schedule
  graded, newer .tex  flag for the instructor; never regraded automatically
For a _CORRECTIONS assignment, a PDF counts only if it is newer than the student's frozen
first-pass time, and a corrections .tex from a student whose first pass exists but is not
graded yet waits for it. A corrections .tex without a new PDF is still graded at the
deadline -- the submission is incomplete, and the feedback email says the corrections
grade will not be applied (a PDF arriving afterwards is flagged). A student with no first pass at all is graded straight away,
which in corrections mode produces the "no original submission" report.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import yaml

STATE_FILE = "watch_state.json"


# --------------------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------------------

_TS = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?")


def parse_ts(value) -> Optional[datetime]:
    """A Gradescope or config timestamp as a naive UTC datetime (as the reporter uses).

    Accepts "2026-01-17 04:15:54.397486000 Z", ISO strings with offsets, naive strings
    (taken as UTC, like due dates in config.yaml) and datetime objects.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.replace(microsecond=0)
    m = _TS.search(str(value))
    if not m:
        return None
    dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
    tz = m.group(4)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        dt -= sign * timedelta(hours=hh, minutes=mm)
    return dt


def gradescope_ts(dt: datetime) -> str:
    """Format a time the way Gradescope does, which is what the reporter parses."""
    return dt.strftime("%Y-%m-%d %H:%M:%S") + ".000000000 Z"


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# --------------------------------------------------------------------------------------
# Snapshot: what the current exports show
# --------------------------------------------------------------------------------------

@dataclass
class Submission:
    id: str
    created_at: datetime
    has_tex: bool = True          # for .tex-assignment submissions: does it contain a .tex file?


@dataclass
class StudentSnapshot:
    key: str                      # SID, or lower-cased email when there is no SID
    name: str
    email: str
    sid: str
    tex: Optional[Submission] = None
    pdf: Optional[Submission] = None
    group_member: bool = False    # on someone else's .tex submission; graded with theirs


def student_key(sid, email) -> str:
    sid = str(sid or "").strip()
    return sid if sid else str(email or "").strip().lower()


def submission_key(sid, email) -> str:
    """student_key() for a StudentSubmission, whose missing SID reads 'None' or 'Unknown'."""
    return student_key("" if str(sid) in ("None", "Unknown") else sid, email)


def _clean(data):
    if isinstance(data, dict):
        return {str(k).lstrip(':'): _clean(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_clean(v) for v in data]
    return data


def _load_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return _clean(yaml.safe_load(f) or {})


def read_snapshot(export_dir: Path, pdf_metadata: Optional[Path] = None) -> Dict[str, StudentSnapshot]:
    """Students in an export directory: its submission_metadata.yml (the .tex assignment)
    and a PDF assignment's metadata (default: pdf_submission_metadata.yml beside it)."""
    export_dir = Path(export_dir)
    pdf_metadata = pdf_metadata or export_dir / "pdf_submission_metadata.yml"
    students: Dict[str, StudentSnapshot] = {}

    def entry(submitter) -> StudentSnapshot:
        key = student_key(submitter.get('sid'), submitter.get('email'))
        if key not in students:
            students[key] = StudentSnapshot(
                key=key, name=submitter.get('name', 'Unknown'),
                email=submitter.get('email', 'Unknown'), sid=str(submitter.get('sid') or ''))
        return students[key]

    for sub_key, sub in _load_meta(export_dir / "submission_metadata.yml").items():
        if not isinstance(sub, dict) or not sub.get('submitters'):
            continue
        created = parse_ts(sub.get('created_at'))
        if created is None:
            continue
        has_tex = any((export_dir / sub_key).glob("*.tex"))
        submission = Submission(id=str(sub_key), created_at=created, has_tex=has_tex)
        for i, submitter in enumerate(sub['submitters']):
            s = entry(submitter)
            if s.tex is None or created > s.tex.created_at:     # latest, if listed twice
                s.tex = submission
                s.group_member = i > 0

    for sub_key, sub in _load_meta(pdf_metadata).items():
        if not isinstance(sub, dict) or not sub.get('submitters'):
            continue
        created = parse_ts(sub.get('created_at'))
        if created is None:
            continue
        for submitter in sub['submitters']:
            s = entry(submitter)
            if s.pdf is None or created > s.pdf.created_at:
                s.pdf = Submission(id=str(sub_key), created_at=created)

    return {k: v for k, v in students.items() if k}


def first_pass_times(base: str, output_root: Path = Path("data/output")):
    """(key, name) -> a student's frozen first-pass submission time on assignment `base`.

    From the watcher's ledger when it graded them; otherwise from grading_metadata.yaml,
    where the reporter recorded the later of the .tex and PDF times at grading. None when
    the student has no graded first pass.
    """
    base_dir = Path(output_root) / f"{base}_Feedback"
    ledger = Ledger(base_dir) if (base_dir / STATE_FILE).exists() else None
    meta_path = base_dir / "grading_metadata.yaml"
    meta = (yaml.safe_load(meta_path.read_text()) or {}) if meta_path.exists() else {}

    def lookup(key: str, name: str) -> Optional[datetime]:
        if ledger is not None:
            status, frozen = ledger.first_pass(key)
            if status == "graded" and frozen:
                return frozen
        safe = name.replace(" ", "_").replace("(", "").replace(")", "")
        return parse_ts((meta.get(safe) or {}).get('submission_created_at'))
    return lookup


def corrections_pdf_check(base: str, pdf_metadata: Path, output_root: Path = Path("data/output")):
    """For corrections feedback: did each student resubmit their compiled PDF?

    Returns check(name, sid, email) -> True if the PDF assignment's metadata shows a PDF
    newer than the student's first-pass submission time, False if it does not, and None
    when that cannot be told (no first pass on record). Returns None instead of a check
    when there is no PDF metadata to look in.
    """
    pdf_metadata = Path(pdf_metadata)
    if not pdf_metadata.exists():
        return None
    latest: Dict[str, datetime] = {}
    for sub in _load_meta(pdf_metadata).values():
        if not isinstance(sub, dict):
            continue
        created = parse_ts(sub.get('created_at'))
        for submitter in sub.get('submitters') or []:
            key = student_key(submitter.get('sid'), submitter.get('email'))
            if created and key and (key not in latest or created > latest[key]):
                latest[key] = created
    first_pass = first_pass_times(base, output_root)

    def check(name: str, sid, email) -> Optional[bool]:
        key = submission_key(sid, email)
        first = first_pass(key, name)
        if first is None:
            return None
        return key in latest and latest[key] > first
    return check


# --------------------------------------------------------------------------------------
# Ledger: what the watcher has done
# --------------------------------------------------------------------------------------

class Ledger:
    """Per-student record for one assignment, kept in <output_dir>/watch_state.json."""

    def __init__(self, output_dir: Path):
        self.path = Path(output_dir) / STATE_FILE
        self.data = {"version": 1, "students": {}}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                # Keep the unreadable file for inspection rather than overwrite it
                self.path.rename(self.path.with_suffix(".corrupt.json"))
        self.data.setdefault("students", {})
        self._repair_interrupted()

    def _repair_interrupted(self):
        """Undo a bug in earlier versions: stopping the watcher mid-batch recorded every
        student in the batch as graded, held with "no report was generated", so they were
        never graded. Such students (no report, never adopted from earlier output) are
        made ungraded again; problems graded before the stop are reused."""
        for st in self.students.values():
            graded = st.get("graded")
            if (graded and not graded.get("seeded") and not st.get("report")
                    and any("no report was generated" in h for h in st.get("held") or [])
                    and not any("attempts" in h for h in st.get("held") or [])):
                del st["graded"]
                st.pop("held", None)
                st["repaired"] = "regrade: earlier run was stopped before this report"

    @property
    def students(self) -> Dict[str, dict]:
        return self.data["students"]

    def get(self, key: str) -> dict:
        return self.students.setdefault(key, {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)          # atomic: a crash never leaves half a ledger

    # -- updates --------------------------------------------------------------------

    def observe(self, snapshot: Dict[str, StudentSnapshot], now: datetime):
        """Record identity and when each submission was first seen."""
        for key, s in snapshot.items():
            st = self.get(key)
            st.update(name=s.name, email=s.email, sid=s.sid)
            seen = st.setdefault("first_seen", {})
            for kind, sub in (("tex", s.tex), ("pdf", s.pdf)):
                if sub is not None:
                    seen.setdefault(f"{kind}:{sub.id}:{iso(sub.created_at)}", iso(now))

    def seed_from_outputs(self, snapshot: Dict[str, StudentSnapshot], output_dir: Path,
                          safe_name: Callable[[str], str]):
        """Adopt students graded before the watcher ran (by grade.py, or a watcher whose
        ledger was lost), so they are not graded twice. Their frozen time is the one the
        reporter recorded, which was the later of .tex and PDF when they were graded."""
        output_dir = Path(output_dir)
        meta_path = output_dir / "grading_metadata.yaml"
        meta = {}
        if meta_path.exists():
            with open(meta_path) as f:
                meta = yaml.safe_load(f) or {}
        delivery_path = output_dir / "email_delivery.yaml"
        delivery = {}
        if delivery_path.exists():
            with open(delivery_path) as f:
                delivery = yaml.safe_load(f) or {}

        for key, s in snapshot.items():
            st = self.get(key)
            name = safe_name(s.name)
            if "graded" not in st:
                reports = [output_dir / f"Feedback_{name}.pdf",
                           *sorted(output_dir.glob(f"Feedback_{name}_v*.pdf"))]
                existing = [r for r in reports if r.exists()]
                if existing:
                    when = parse_ts((meta.get(name) or {}).get('submission_created_at'))
                    st["graded"] = {"at": None, "seeded": True, "effective": iso(when),
                                    "tex_created_at": iso(when), "pdf_created_at": None}
                    st["report"] = str(existing[-1])
            # Deliveries made outside the watcher (post_grading.py send-feedback) count too
            if name in (delivery.get('delivered') or {}) and not st.get("feedback_sent_at"):
                st["feedback_sent_at"] = delivery['delivered'][name].get('sent_at') or "before watcher"
            for kind in ("missing_tex", "missing_pdf", "missing_tex_corrections"):
                if name in (delivery.get(f'{kind}_notified') or {}):
                    st.setdefault("notices", {}).setdefault(kind, delivery[f'{kind}_notified'][name].get('sent_at'))

    def mark_graded(self, key: str, s: StudentSnapshot, effective: datetime,
                    pdf: Optional[Submission], now: datetime, report: Optional[Path],
                    hold: List[str]):
        st = self.get(key)
        st["graded"] = {"at": iso(now), "seeded": False, "effective": iso(effective),
                        "tex_created_at": iso(s.tex.created_at) if s.tex else None,
                        "tex_id": s.tex.id if s.tex else None,
                        "pdf_created_at": iso(pdf.created_at) if pdf else None}
        st["report"] = str(report) if report else None
        if hold:
            st["held"] = hold
        else:
            st.pop("held", None)

    def first_pass(self, key: str) -> Tuple[str, Optional[datetime]]:
        """('graded', frozen time) | ('pending', None) | ('none', None), for corrections."""
        st = self.students.get(key) or {}
        graded = st.get("graded")
        if graded:
            return "graded", parse_ts(graded.get("effective"))
        return "pending", None


# --------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------

@dataclass
class Policy:
    due: datetime                                   # naive UTC, as in config.yaml
    settle: timedelta = timedelta(minutes=15)
    notice_after: timedelta = timedelta(hours=1)
    extensions: Dict[str, float] = field(default_factory=dict)   # student name -> hours
    corrections: bool = False
    # For corrections: the base assignment's view of a student
    first_pass: Optional[Callable[[str], Tuple[str, Optional[datetime]]]] = None

    def deadline_for(self, s: StudentSnapshot) -> datetime:
        """The student's own deadline, extension included: when their late penalty starts
        and when an incomplete submission is worth a notice. Grading waits only for the
        assignment's due date (`due`), so an extended student who has submitted is graded
        with everyone else."""
        return self.due + timedelta(hours=self.extensions.get(s.name, 0))


@dataclass
class Action:
    kind: str                   # grade | notice | flag
    key: str
    name: str
    detail: str = ""            # notice kind, or flag message
    created_at: Optional[datetime] = None   # grade: the submission time to charge
    pdf: Optional[Submission] = None        # grade: the PDF that time came from
    flag_id: str = ""


@dataclass
class Waiting:
    key: str
    name: str
    reason: str
    until: Optional[datetime] = None


def decide(ledger: Ledger, snapshot: Dict[str, StudentSnapshot], policy: Policy,
           now: datetime) -> Tuple[List[Action], List[Waiting]]:
    actions: List[Action] = []
    waiting: List[Waiting] = []
    suffix = "_corrections" if policy.corrections else ""

    def wait(s, reason, until=None):
        waiting.append(Waiting(s.key, s.name, reason, until))

    def notice(s, kind, due_at, reason):
        kind = kind + suffix
        if (ledger.students.get(s.key) or {}).get("notices", {}).get(kind):
            wait(s, f"{reason}; notified")
        elif now >= due_at:
            actions.append(Action("notice", s.key, s.name, detail=kind))
        else:
            wait(s, f"{reason}; notice due", due_at)

    def flag(s, flag_id, message):
        if flag_id not in (ledger.students.get(s.key) or {}).get("flags", {}):
            actions.append(Action("flag", s.key, s.name, detail=message, flag_id=flag_id))

    def grade_when_ready(s, created_at, pdf, last_activity):
        deadline = policy.due                     # not the student's extended deadline
        ready_at = max(deadline, last_activity + policy.settle)
        if now >= ready_at:
            actions.append(Action("grade", s.key, s.name, created_at=created_at, pdf=pdf))
        else:
            wait(s, "before the deadline" if now < deadline else "settling (recent submission)", ready_at)

    for key, s in snapshot.items():
        if s.group_member:
            continue                                    # graded with the group's submission
        st = ledger.students.get(key) or {}
        graded = st.get("graded")
        tex_ok = s.tex is not None and s.tex.has_tex
        deadline = policy.deadline_for(s)

        if graded:
            graded_tex = parse_ts(graded.get("tex_created_at") or graded.get("effective"))
            if tex_ok and graded_tex and s.tex.created_at > graded_tex:
                within = (" This is within their extension, so it may be deliberate."
                          if s.tex.created_at <= deadline and deadline > policy.due else "")
                flag(s, f"resubmission:{s.tex.id}:{iso(s.tex.created_at)}",
                     f"submitted a new .tex at {iso(s.tex.created_at)} UTC after being graded "
                     f"(graded submission from {iso(graded_tex)} UTC). Not regraded.{within}")
            if policy.corrections and not graded.get("seeded") and not graded.get("pdf_created_at") \
                    and s.pdf is not None and policy.first_pass is not None:
                status, frozen = policy.first_pass(key)
                if status == "graded" and frozen and s.pdf.created_at > frozen:
                    flag(s, f"late-corrections-pdf:{s.pdf.id}",
                         f"resubmitted their PDF at {iso(s.pdf.created_at)} UTC, after corrections "
                         f"feedback went out saying the corrections grade would not be applied "
                         f"without it. Decide whether to apply it.")
            continue

        if s.tex is not None and not s.tex.has_tex and s.pdf is None:
            flag(s, f"no-tex-file:{s.tex.id}",
                 "submitted to the .tex assignment without a .tex file, and no PDF")
            continue

        if policy.corrections:
            status, frozen = (policy.first_pass or (lambda k: ("none", None)))(key)
            pdf = s.pdf if (s.pdf and (frozen is None or s.pdf.created_at > frozen)) else None
            if tex_ok:
                if status == "pending":
                    wait(s, "first pass not graded yet")
                elif status == "none":
                    grade_when_ready(s, s.tex.created_at, None, s.tex.created_at)
                elif pdf:
                    grade_when_ready(s, max(s.tex.created_at, pdf.created_at), pdf,
                                     max(s.tex.created_at, pdf.created_at))
                else:
                    # No new PDF: the corrections are incomplete. Graded anyway at the
                    # deadline; the feedback email says the grade will not be applied.
                    grade_when_ready(s, s.tex.created_at, None, s.tex.created_at)
            elif pdf and status == "graded":
                notice(s, "missing_tex", max(deadline, pdf.created_at + policy.notice_after),
                       "new PDF, no corrections .tex")
            continue

        if tex_ok and s.pdf:
            last = max(s.tex.created_at, s.pdf.created_at)
            grade_when_ready(s, last, s.pdf, last)
        elif tex_ok:
            notice(s, "missing_pdf", max(deadline, s.tex.created_at + policy.notice_after),
                   "waiting for PDF")
        elif s.pdf:
            reason = ("its .tex submission contains no .tex file" if s.tex is not None
                      else "waiting for .tex")
            notice(s, "missing_tex", max(deadline, s.pdf.created_at + policy.notice_after), reason)

    return actions, waiting
