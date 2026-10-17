"""A stand-in for lata.gradescope.Gradescope, behaving like the real pages.

A PDF assignment (20) with a negatively scored LaTA question (300, 8 points, "Correct"
and "No .tex" rubric items) and a hand-graded sketch question (301, 2 points), and a .tex
assignment (10). Times are entered as Gradescope's local -0700 times.
"""
from datetime import datetime, timedelta

import fx
from lata.gradescope import Question

NAMES = {"1": "Jane Doe", "2": "John Smith", "3": "Kim Lee", "5": "Lee Park", "6": "Max Ray"}
WEIGHT = 8.0


def utc(local):
    return (datetime.strptime(local, "%Y-%m-%d %H:%M:%S") + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")


class FakeGS:
    def __init__(self):
        self.tex, self.pdf = {}, {}       # sid -> (asid, local time)
        self.evals = {}                   # qsid -> {"rubric": set, "points": float, "graded": bool}
        self.sketch_graded = set()
        self.published = False
        self.canvas_posts = 0
        self.saves = []
        self.exports = 0
        self.scoring = "negative"

    def row(self, sid, sub):
        first, last = NAMES[sid].split()
        r = {"First Name": first, "Last Name": last, "SID": sid, "Email": f"{last.lower()}{sid}@oregonstate.edu",
             "Submission ID": "", "Submission Time": "", "Status": "Missing"}
        if sub:
            asid, t = sub
            r.update({"Submission ID": str(asid), "Submission Time": t + " -0700"})
            r["Status"] = "Graded" if (self.evals.get(asid * 10, {}).get("graded") and sid in self.sketch_graded) else "Ungraded"
        return r

    def scores(self, course, a):
        subs = self.tex if a == 10 else self.pdf
        return [self.row(sid, subs.get(sid)) for sid in NAMES]

    def questions(self, course, a):
        return [Question(300, "HW2", WEIGHT), Question(301, "sketch", 2.0)]

    def question_submission_ids(self, course, q):
        return [asid * 10 for asid, _ in self.pdf.values()]

    def score_of(self, qsid):
        e = self.evals.get(qsid)
        if not e or not e["graded"]:
            return 0.0
        base = WEIGHT if self.scoring == "negative" else 0.0
        return max(0.0, base + sum(-WEIGHT for r in e["rubric"] if r == 2) + e["points"])

    def grading_page(self, course, q, qsid):
        e = self.evals.get(qsid, {"graded": False, "points": 0.0, "rubric": set()})
        return {"submission": {"assignment_submission_id": qsid // 10, "score": str(self.score_of(qsid)), "graded": e["graded"]},
                "evaluation": {"points": str(e["points"])},
                "rubric_items": [{"id": 1, "description": "Correct", "weight": "0.0"},
                                 {"id": 2, "description": "No .tex", "weight": str(-WEIGHT)}],
                "question": {"weight": str(WEIGHT), "scoring_type": self.scoring, "floor": True, "ceiling": False},
                "assignment": {"published": self.published}}

    def save_grade(self, course, q, qsid, rubric, points, comments=None):
        self.saves.append((qsid, sorted(k for k, v in rubric.items() if v), points))
        self.evals[qsid] = {"rubric": {k for k, v in rubric.items() if v}, "points": points, "graded": True}

    def export_submissions(self, course, a, dest):
        self.exports += 1
        fx.tex_zip(dest, {asid: (NAMES[sid], sid, utc(t)) for sid, (asid, t) in self.tex.items()})
        return dest

    def post_grades_to_canvas(self, course, a):
        self.canvas_posts += 1
        return {"complete": True}

    def save(self):
        pass

    def close(self):
        pass


def install(gs):
    """Make the watcher use `gs` instead of a real Gradescope session."""
    import lata.watch as W
    from lata.gradescope_sync import GradescopeSource, GradeEntry

    def fake_session(self):
        if self.gs is None:
            self.gs = gs
            self.source = GradescopeSource(gs, 1, self.settings['inbox_dir'])
            self.grade_entry = GradeEntry(gs, 1)
        return self.gs
    W.Watcher.gradescope = fake_session
