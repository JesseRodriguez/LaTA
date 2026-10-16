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
Add Gradescope assignments to the watcher's config, without looking anything up by hand.

    uv run python -m lata.setup_watch                 # guided: pick, check, add
    uv run python -m lata.setup_watch --list          # just show what is on Gradescope

It reads the course's assignment list from Gradescope, pairs each ".tex" assignment with
its PDF assignment by title ("Homework #2 (561) .tex file" with "Homework #2 (561)";
corrections, "... .tex file (CORRECTIONS)", with the original homework's PDF assignment,
where corrections PDFs go), takes the due date from Gradescope (converted to GMT, as the
config wants), suggests the LaTA name, rubric and solution from what is in data/rubrics
and data/solutions, and appends the entries you confirm to the config's `assignments:`.

Titles without a course number ("Homework #2") are taken to belong to the course number
in the Gradescope course name that no other title is tagged with ("ME 461/561" with
"(561)" tags elsewhere -> 461). Every suggestion can be changed before it is written.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

TEX = re.compile(r"\.tex\b", re.I)
CORR = re.compile(r"correction", re.I)


def base_title(title: str) -> str:
    t = re.sub(r"\.tex\s*files?", "", title, flags=re.I)
    t = re.sub(r"\(\s*corrections?\s*\)|\bcorrections?\b", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -_").lower()


@dataclass
class GSAssignment:
    id: int
    title: str
    due_local: Optional[str]          # course-local "2026-10-09T22:00"
    hard_due_local: Optional[str]
    published: bool
    due_utc: Optional[str] = None     # "2026-10-10 05:00:00", as the config wants

    @property
    def is_tex(self) -> bool:
        return bool(TEX.search(self.title))

    @property
    def is_corrections(self) -> bool:
        return bool(CORR.search(self.title))


@dataclass
class Pair:
    tex: GSAssignment
    pdf: Optional[GSAssignment]

    @property
    def corrections(self) -> bool:
        return self.tex.is_corrections

    @property
    def title(self) -> str:
        return self.tex.title


@dataclass
class Suggestion:
    name: str
    rubric: Optional[str]
    solution: Optional[str]
    rubric_choices: List[str] = field(default_factory=list)
    solution_choices: List[str] = field(default_factory=list)


def to_utc(local: Optional[str], tz_name: str) -> Optional[str]:
    if not local:
        return None
    from zoneinfo import ZoneInfo
    dt = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fetch_assignments(gs, course: int) -> Tuple[List[GSAssignment], str, str]:
    """(assignments, course name, time zone) from the course's Assignments page."""
    page = gs.text(f"/courses/{course}/assignments")
    props = gs._react_props(page, "AssignmentsTable")
    tz = (props.get("timezone") or {}).get("identifier") or "UTC"
    out = []
    for row in props.get("table_data", []):
        if row.get("type") != "assignment" or not str(row.get("id", "")).startswith("assignment_"):
            continue
        w = row.get("submission_window") or {}
        a = GSAssignment(id=int(row["id"].split("_", 1)[1]), title=row.get("title", ""),
                         due_local=w.get("due_date"), hard_due_local=w.get("hard_due_date"),
                         published=bool(row.get("is_published")))
        a.due_utc = to_utc(a.due_local, tz)
        out.append(a)
    m = re.search(r"<title>(.*?)\|", gs.text(f"/courses/{course}"))
    name = (m.group(1).replace("Dashboard", "").strip() if m else "")
    return out, name, tz


def pair_assignments(assignments: List[GSAssignment]) -> List[Pair]:
    """Each .tex assignment with the PDF assignment of the same (base) title."""
    pdfs = {base_title(a.title): a for a in assignments if not a.is_tex}
    return [Pair(tex=a, pdf=pdfs.get(base_title(a.title))) for a in assignments if a.is_tex]


def _numbers(text: str) -> List[str]:
    return re.findall(r"(?<!\d)(\d{3})(?!\d)", text)


def _hw(text: str) -> Optional[str]:
    m = re.search(r"(?:homework|hw)\s*#?\s*(\d+)", text, re.I)
    return m.group(1) if m else None


def course_number_for(title: str, course_name: str, all_titles: List[str]) -> Optional[str]:
    tagged = _numbers(title)
    if tagged:
        return tagged[0]
    used = {n for t in all_titles for n in _numbers(t)}
    untagged = [n for n in _numbers(course_name) if n not in used]
    return untagged[0] if len(untagged) == 1 else None


def _matches(path: str, hw: Optional[str], number: Optional[str]) -> int:
    """How well a file name fits (homework number required; course number preferred)."""
    stem = Path(path).name.upper()
    if not hw or not re.search(rf"HW\D?0*{hw}(?!\d)", stem):
        return -1
    score = 1
    if number and number in stem:
        score += 2
    elif number and _numbers(stem):
        return -1                     # names a different course
    return score


def suggest(pair: Pair, course_name: str, all_titles: List[str],
            rubrics_dir: Path, solutions_dir: Path) -> Suggestion:
    hw = _hw(pair.title)
    number = course_number_for(pair.title, course_name, all_titles)

    def ranked(paths, prefer_corrections):
        scored = []
        for p in paths:
            s = _matches(p, hw, number)
            if s < 0:
                continue
            is_corr = "CORRECTION" in Path(p).name.upper()
            if is_corr and not prefer_corrections:
                continue
            scored.append((s + (1 if is_corr == prefer_corrections else 0), p))
        return [p for _, p in sorted(scored, key=lambda x: (-x[0], x[1]))]

    rubrics = ranked(sorted(p.name for p in Path(rubrics_dir).glob("*_rubric.yaml")), pair.corrections)
    solutions = ranked(sorted(str(p) for p in Path(solutions_dir).glob("*.tex")), pair.corrections)
    rubric = rubrics[0] if rubrics else None
    if rubric:
        name = re.sub(r"_rubric\.yaml$", "", rubric)
    else:
        name = f"ME{number or ''}_HW{hw or ''}"
    if pair.corrections and not name.upper().endswith("_CORRECTIONS"):
        name += "_CORRECTIONS"
    return Suggestion(name=name, rubric=rubric, solution=solutions[0] if solutions else None,
                      rubric_choices=rubrics, solution_choices=solutions)


def entry_text(name: str, rubric: str, solution: str, due_utc: str, pair: Pair, local_due: str, tz: str) -> str:
    return (f"  - name: {name}\n"
            f"    rubric_file: {rubric}\n"
            f"    solution_file: {solution}\n"
            f"    due_date: \"{due_utc}\"            # GMT ({local_due.replace('T', ' ')} {tz})\n"
            f"    gradescope: {{tex: {pair.tex.id}, pdf: {pair.pdf.id}}}   # {pair.tex.title} / {pair.pdf.title}\n")


def add_entries(config_path: Path, entries: List[str], course: int) -> List[str]:
    """Append entries to the config's top-level `assignments:` list (creating it, and a
    gradescope `watch:` block, if missing). Comments and layout elsewhere are kept.
    Returns notes about anything it could not set up itself."""
    text = config_path.read_text() if config_path.exists() else ""
    lines = text.splitlines(keepends=True)
    notes = []
    start = next((i for i, l in enumerate(lines) if re.match(r"^assignments:\s*(#.*)?$", l)), None)
    block = "".join(entries)
    if start is None:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\nassignments:\n" + block
    else:
        end = start + 1                      # the list ends at the next top-level line
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#-"):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1                         # keep blank lines after the list where they were
        lines[end:end] = [block]
        text = "".join(lines)
    data = yaml.safe_load(text) or {}
    watch = data.get("watch") or {}
    if "watch" not in data:
        text += ("\nwatch:\n  source: gradescope\n  gradescope:\n"
                 f"    course: {course}\n  notify_email: null             # your address, for \"needs attention\" emails\n")
    elif watch.get("source") != "gradescope" or (watch.get("gradescope") or {}).get("course") != course:
        notes.append(f"watch: needs `source: gradescope` and `gradescope: {{course: {course}}}` -- "
                     f"check the existing watch: block.")
    yaml.safe_load(text)                     # never write a config that does not parse
    config_path.write_text(text)
    return notes


def already_configured(config: dict) -> Dict[int, str]:
    return {int((a.get("gradescope") or {}).get("tex")): a.get("name")
            for a in (config.get("assignments") or []) if (a.get("gradescope") or {}).get("tex")}


def ask(prompt: str, default: Optional[str] = None) -> str:
    answer = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return answer or (default or "")


def pick_file(label: str, choices: List[str], default: Optional[str]) -> str:
    if len(choices) > 1:
        print(f"    {label} candidates: " + ", ".join(f"({i}) {c}" for i, c in enumerate(choices, 1)))
    answer = ask(f"    {label}", default)
    if answer.isdigit() and 1 <= int(answer) <= len(choices):
        return choices[int(answer) - 1]
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(description="Add Gradescope assignments to the watch config")
    parser.add_argument("--config", default=None,
                        help="Config to add to (default: config.watch.yaml if it exists, else config.yaml)")
    parser.add_argument("--course", type=int, default=None, help="Gradescope course id (default: from the config)")
    parser.add_argument("--list", action="store_true", help="Only list the course's assignments and pairs")
    args = parser.parse_args(argv)

    config_path = Path(args.config or ("config.watch.yaml" if Path("config.watch.yaml").exists() else "config.yaml"))
    config = (yaml.safe_load(config_path.read_text()) or {}) if config_path.exists() else {}
    course = args.course or ((config.get("watch") or {}).get("gradescope") or {}).get("course")
    if not course:
        course = int(ask("Gradescope course id (the number in the course's URL)"))
    paths = config.get("paths") or {}
    rubrics_dir = Path(paths.get("rubrics_dir", "data/rubrics"))
    solutions_dir = Path("data/solutions")

    from lata.gradescope import Gradescope, GradescopeError
    try:
        with Gradescope(((config.get("watch") or {}).get("gradescope") or {}).get("profile_dir")) as gs:
            assignments, course_name, tz = fetch_assignments(gs, int(course))
    except GradescopeError as e:
        print(f"❌ {e}")
        return 1

    pairs = pair_assignments(assignments)
    titles = [a.title for a in assignments]
    have = already_configured(config)
    print(f"\n{course_name or 'Course'} ({course}) -- times in {tz}\n")
    print(f"  {'#':>2}  {'.tex assignment':<46} {'PDF assignment':<26} {'due':<17} in {config_path.name}")
    for i, p in enumerate(pairs, 1):
        due = (p.tex.due_local or "").replace("T", " ")
        pdf = p.pdf.title if p.pdf else "(no matching PDF assignment)"
        print(f"  {i:>2}  {p.tex.title[:46]:<46} {pdf[:26]:<26} {due:<17} {have.get(p.tex.id, '')}")
    if args.list:
        return 0

    picked = ask("\nAdd which? (numbers like 3,5 -- or Enter to cancel)")
    if not picked:
        print("Nothing added.")
        return 0
    entries = []
    for token in re.split(r"[,\s]+", picked):
        if not token.isdigit() or not 1 <= int(token) <= len(pairs):
            print(f"  skipping {token!r}: not a number in the list")
            continue
        p = pairs[int(token) - 1]
        if p.pdf is None:
            print(f"  skipping {p.title!r}: no PDF assignment with a matching title")
            continue
        if p.tex.id in have:
            print(f"  skipping {p.title!r}: already in {config_path.name} as {have[p.tex.id]}")
            continue
        s = suggest(p, course_name, titles, rubrics_dir, solutions_dir)
        print(f"\n  {p.title}  (+ PDF: {p.pdf.title})")
        name = ask("    LaTA name", s.name)
        rubric = pick_file("rubric file", s.rubric_choices, s.rubric)
        solution = pick_file("solution file", s.solution_choices, s.solution)
        if not rubric or not solution:
            print(f"    skipping {name}: it needs a rubric and a solution file (type their names to add it ahead of time)")
            continue
        due = ask(f"    due date, GMT (Gradescope: {(p.tex.due_local or '?').replace('T', ' ')} {tz})", p.tex.due_utc)
        for label, path in (("rubric", rubrics_dir / rubric), ("solution", Path(solution))):
            if not path.exists():
                print(f"    ⚠️  {label} {path} does not exist yet -- the watcher will not start until it does")
        first = name[:-len("_CORRECTIONS")] if name.upper().endswith("_CORRECTIONS") else None
        if first and not any(a.get("name") == first for a in config.get("assignments") or []) \
                and not Path(f"data/output/{first}_Feedback").exists():
            print(f"    ⚠️  its first pass {first} is neither in the config nor graded "
                  f"(data/output/{first}_Feedback) -- the watcher will not start until one is true")
        entries.append(entry_text(name, rubric, solution, due, p, p.tex.due_local or "", tz))

    if not entries:
        print("Nothing added.")
        return 0
    print("\nThis will be added to " + str(config_path) + ":\n\n" + "".join(entries))
    if ask("Write it? (y/n)", "n").lower() not in ("y", "yes"):
        print("Nothing written.")
        return 0
    for note in add_entries(config_path, entries, int(course)):
        print(f"⚠️  {note}")
    print(f"✅ Added {len(entries)} assignment(s) to {config_path}. "
          f"Check it with: uv run grade.py --config {config_path} --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
