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
Talk to Gradescope the way its own web pages do.

Gradescope has no public API for instructors, so this drives the same requests the site
makes from your browser: the grades CSV, "Export Submissions", the grading page's save,
and "Post grades to Canvas". It needs a logged-in session, which you create once:

    uv run --extra gradescope python -m lata.gradescope login

That opens a browser window; sign in there (SSO works, since it is a real browser) and it
closes once Gradescope shows you are logged in. The session's cookies are saved to
~/.lata/gradescope-profile/session.json (readable only by you) and reused headlessly from
then on. Gradescope's login cookie lasts only as long as the browser, so it has to be
saved explicitly; each use saves it back, keeping it current. LaTA never sees or stores
your password. When the session eventually expires, run login again.

Everything here acts as you. Writes are limited to saving a question's grade and posting
grades to Canvas; nothing here publishes grades, changes settings or deletes anything.
"""

import csv
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

BASE = "https://www.gradescope.com"
DEFAULT_PROFILE = "~/.lata/gradescope-profile"


class GradescopeError(RuntimeError):
    pass


class NotLoggedIn(GradescopeError):
    pass


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise GradescopeError(
            "Playwright is not installed. Install the Gradescope extra:\n"
            "  uv sync --extra gradescope && uv run playwright install chromium")
    return sync_playwright


def profile_dir(configured: Optional[str] = None) -> Path:
    path = Path(configured or DEFAULT_PROFILE).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)                      # it holds your session cookies
    return path


def _save_state(context, path: Path):
    tmp = path.with_suffix(".tmp")
    context.storage_state(path=str(tmp))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def login(profile: Optional[str] = None, timeout_minutes: int = 15) -> bool:
    """Open a browser window, let the instructor sign in, keep the session."""
    sync_playwright = _playwright()
    path = profile_dir(profile)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(path), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"{BASE}/login")
        print("A browser window has opened. Sign in to Gradescope there (SSO is fine).")
        print(f"Waiting up to {timeout_minutes} minutes...")
        deadline = time.time() + timeout_minutes * 60
        ok = False
        while time.time() < deadline:
            try:
                r = context.request.get(f"{BASE}/account", max_redirects=0)
                if r.status == 200:
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(3)
        if ok:
            _save_state(context, path / "session.json")
        context.close()
    print("✅ Logged in; session saved." if ok else "❌ Timed out before sign-in completed.")
    return ok


@dataclass
class Question:
    id: int
    title: str
    weight: float


class Gradescope:
    """A headless, logged-in Gradescope session. Use as a context manager."""

    def __init__(self, profile: Optional[str] = None):
        self.profile = profile_dir(profile)
        self.state_path = self.profile / "session.json"
        self._pw = None
        self._browser = None
        self.context = None
        self._csrf = None

    def __enter__(self):
        if not self.state_path.exists():
            raise NotLoggedIn("No saved Gradescope session: run `python -m lata.gradescope login`")
        sync_playwright = _playwright()
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
            self.context = self._browser.new_context(storage_state=str(self.state_path))
            self.check_login()
        except Exception:
            self.close(save=False)
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def save(self):
        """Write the current cookies back, so a session Gradescope refreshed stays usable."""
        if self.context is not None:
            _save_state(self.context, self.state_path)

    def close(self, save: bool = True):
        if self.context is not None:
            if save:
                try:
                    self.save()
                except Exception:
                    pass
            self.context.close()
            self.context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    # -- plumbing ---------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else BASE + path

    def get(self, path: str, **kw):
        r = self.context.request.get(self._url(path), **kw)
        if r.url.rstrip("/").endswith("/login") or r.status == 401:
            raise NotLoggedIn("Gradescope session expired: run `python -m lata.gradescope login`")
        if not r.ok:
            raise GradescopeError(f"GET {path}: HTTP {r.status}")
        return r

    def text(self, path: str) -> str:
        return self.get(path).text()

    def csrf(self) -> str:
        if self._csrf is None:
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', self.text("/account"))
            if not m:
                raise GradescopeError("could not find Gradescope's CSRF token")
            self._csrf = m.group(1)
        return self._csrf

    def post(self, path: str, data=None, json_body=None):
        headers = {"X-CSRF-Token": self.csrf(), "X-Requested-With": "XMLHttpRequest",
                   "Accept": "application/json"}
        kw = {"headers": headers}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            kw["data"] = json.dumps(json_body)
        elif data is not None:
            kw["form"] = data
        r = self.context.request.post(self._url(path), **kw)
        if r.status in (401, 422):
            self._csrf = None                     # stale token; caller may retry once
        if not r.ok:
            raise GradescopeError(f"POST {path}: HTTP {r.status}: {r.text()[:200]}")
        return r

    def check_login(self):
        r = self.context.request.get(f"{BASE}/account", max_redirects=0)
        if r.status != 200:
            raise NotLoggedIn("Not logged in to Gradescope: run `python -m lata.gradescope login`")

    @staticmethod
    def _react_props(html: str, cls: str) -> dict:
        m = re.search(r'data-react-class="%s"\s+data-react-props="([^"]*)"' % re.escape(cls), html)
        if not m:
            raise GradescopeError(f"page has no {cls} data (has Gradescope changed?)")
        import html as htmllib
        return json.loads(htmllib.unescape(m.group(1)))

    # -- reads ------------------------------------------------------------------------

    def scores(self, course: int, assignment: int) -> List[Dict[str, str]]:
        """The assignment's grades CSV: one row per student, with SID, email, submission
        ID, submission time ("2026-10-07 16:22:20 -0700"), lateness, status and scores."""
        return list(csv.DictReader(io.StringIO(self.text(f"/courses/{course}/assignments/{assignment}/scores.csv"))))

    def questions(self, course: int, assignment: int) -> List[Question]:
        props = self._react_props(self.text(f"/courses/{course}/assignments/{assignment}/grade"),
                                  "GradingDashboard")["presenter"]
        a = props["assignments"][str(assignment)]
        leaves = set(a["leaf_question_ids"])
        return [Question(id=q["id"], title=q["title"], weight=float(q["weight"]))
                for q in a["questions"].values() if q["id"] in leaves]

    def question_submission_ids(self, course: int, question: int) -> List[int]:
        html = self.text(f"/courses/{course}/questions/{question}/submissions")
        ids = re.findall(rf"/questions/{question}/submissions/(\d+)/grade", html)
        return list(dict.fromkeys(int(i) for i in ids))

    def grading_page(self, course: int, question: int, qsid: int) -> dict:
        """The grading page's data: submission (incl. assignment_submission_id, score,
        graded), evaluation (points = the point adjustment), rubric_items, assignment
        (incl. published), question (weight, scoring_type, floor) and urls.save_grade."""
        return self._react_props(self.text(f"/courses/{course}/questions/{question}/submissions/{qsid}/grade"),
                                 "SubmissionGrader")

    def course_timezone(self, course: int) -> str:
        """The course's time zone (Gradescope shows and stores due dates in it)."""
        cache = self.__dict__.setdefault("_tz", {})
        if course not in cache:
            props = self._react_props(self.text(f"/courses/{course}/assignments"), "AssignmentsTable")
            cache[course] = (props.get("timezone") or {}).get("identifier") or "UTC"
        return cache[course]

    def extensions(self, course: int, assignment: int) -> List[Dict[str, Optional[str]]]:
        """Per-student extensions on an assignment (its Extensions page).

        [{'user_id', 'name', 'email', 'due', 'type'}] -- `due` is the student's extended
        due date in the course's time zone ("2026-10-23T22:52"), `type` Gradescope's kind
        of value ('absolute' for a date)."""
        page = self.text(f"/courses/{course}/assignments/{assignment}/extensions")
        try:
            props = self._react_props(page, "AddExtension")
        except GradescopeError:
            return []
        people = {str(p["id"]): p for p in props.get("students") or []}
        out = []
        for uid, override in (props.get("overrides") or {}).items():
            due = (override.get("settings") or {}).get("due_date") or {}
            person = people.get(str(uid), {})
            out.append({"user_id": str(uid), "name": person.get("name"), "email": person.get("email"),
                        "due": due.get("value"), "type": due.get("type")})
        return out

    def export_submissions(self, course: int, assignment: int, dest: Path,
                           timeout_minutes: int = 20) -> Path:
        """Gradescope's "Export Submissions": build the zip on the server, wait, download."""
        r = self.post(f"/courses/{course}/assignments/{assignment}/export")
        file_id = r.json()["generated_file_id"]
        deadline = time.time() + timeout_minutes * 60
        while time.time() < deadline:
            status = self.get(f"/courses/{course}/generated_files/{file_id}.json").json()
            if status.get("status") == "completed":
                break
            if status.get("status") == "failed":
                raise GradescopeError(f"export of assignment {assignment} failed on Gradescope")
            time.sleep(5)
        else:
            raise GradescopeError(f"export of assignment {assignment} did not finish in {timeout_minutes} min")
        body = self.get(f"/courses/{course}/generated_files/{file_id}.zip").body()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        return dest

    # -- writes -----------------------------------------------------------------------

    def save_grade(self, course: int, question: int, qsid: int,
                   rubric: Dict[int, bool], points: float, comments: Optional[str] = None) -> dict:
        """Set a question submission's grade: which rubric items are applied (every item of
        the question must be listed) and the point adjustment."""
        body = {"rubric_items": {str(k): {"score": "true" if v else "false"} for k, v in rubric.items()},
                "question_submission_evaluation": {"points": points, "comments": comments}}
        path = f"/courses/{course}/questions/{question}/submissions/{qsid}/save_grade"
        try:
            r = self.post(path, json_body=body)
        except GradescopeError:
            if self._csrf is None:                 # token was stale; one retry
                r = self.post(path, json_body=body)
            else:
                raise
        return r.json() if r.text().strip().startswith("{") else {}

    def post_grades_to_canvas(self, course: int, assignment: int, timeout_minutes: int = 10) -> dict:
        """The Review Grades page's "Post grades to Canvas" (LTI 1.3), waiting for the job."""
        r = self.post(f"/courses/{course}/assignments/{assignment}/integrations/ltiv1p3/grades")
        jid = r.json().get("jid")
        if not jid:
            raise GradescopeError("Canvas post returned no job id")
        deadline = time.time() + timeout_minutes * 60
        while time.time() < deadline:
            status = self.get("/integrations/job_status", params={"jid": jid}).json()
            if status.get("failed"):
                raise GradescopeError(f"Canvas post failed: {status}")
            if status.get("complete"):
                return status
            time.sleep(5)
        raise GradescopeError(f"Canvas post did not finish in {timeout_minutes} min")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="LaTA's Gradescope session")
    sub = parser.add_subparsers(dest="cmd")
    p_login = sub.add_parser("login", help="Sign in once in a browser window; the session is kept")
    p_login.add_argument("--profile", default=None)
    p_check = sub.add_parser("check", help="Check that the saved session still works")
    p_check.add_argument("--profile", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "login":
        sys.exit(0 if login(args.profile) else 1)
    if args.cmd == "check":
        try:
            with Gradescope(args.profile):
                print("✅ Gradescope session is valid.")
        except GradescopeError as e:
            print(f"❌ {e}")
            sys.exit(1)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
