"""Synthetic Gradescope exports, a fake grader and a fake mail server for LaTA's tests.

No real student data: every student here is made up. Tests run in a temporary working
directory (see workdir()), never in the repository's data/ folders.
"""
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

LATA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LATA / "src"))

RUBRIC = {f"Problem {n}": [{"title": f"Problem {n} (a)", "items": [
    {"name": "Correct setup.", "points": 2}, {"name": "Correct answer.", "points": 3}]}] for n in (1, 2)}
SOLUTION = r"\documentclass{article}\begin{document}\Prob{1} x = 1 \Prob{2} y = 2 \end{document}"
STUDENT_TEX = r"\documentclass{article}\begin{document}\Prob{1} x = 1 \Prob{2} y = 2 \end{document}"

_checks = [0]


def check(cond, label):
    print(("  ✓ " if cond else "  ✗ ") + label)
    if not cond:
        raise SystemExit(1)
    _checks[0] += 1


def done():
    print(f"\n{_checks[0]} checks passed")


def ts(s):
    """Gradescope's export timestamp format."""
    return f"{s}.123456000 Z"


def sub(name, sid, created):
    return {':submitters': [{':name': name, ':sid': sid,
                             ':email': f"{name.split()[-1].lower()}{sid}@oregonstate.edu"}],
            ':created_at': ts(created)}


def tex_zip(path, students, tex_ok=None):
    """students: {sub_id: (name, sid, created)}; tex_ok: sub_ids that get a .tex (default all)."""
    meta = {f"submission_{k}": sub(*v) for k, v in students.items()}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("export/submission_metadata.yml", yaml.safe_dump(meta))
        for k in students:
            if tex_ok is None or k in tex_ok:
                z.writestr(f"export/submission_{k}/main.tex", STUDENT_TEX)
            else:
                z.writestr(f"export/submission_{k}/hw.pdf", b"%PDF-1.4")


def pdf_zip(path, students):
    meta = {f"submission_{k}": sub(*v) for k, v in students.items()}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("export/submission_metadata.yml", yaml.safe_dump(meta))
        for k in students:
            z.writestr(f"export/submission_{k}/hw.pdf", b"%PDF-1.4")


def workdir(config_extra=None) -> Path:
    """A fresh temporary LaTA working directory (templates + config); chdir into it."""
    import os
    wd = Path(tempfile.mkdtemp(prefix="lata-test-"))
    for d in ("data/rubrics", "data/solutions", "data/input"):
        (wd / d).mkdir(parents=True)
    shutil.copytree(LATA / "templates", wd / "templates")
    cfg = yaml.safe_load(open(LATA / "config.example.yaml"))
    cfg['system']['grader'] = {'model_name': 'fake', 'is_reasoning_model': False, 'ollama_options': {}}
    cfg['grading']['concurrency'] = 1
    cfg['email']['backend'] = 'smtp'
    cfg['email']['retry_delay'] = 0
    cfg['email']['batch_delay'] = 0
    if config_extra:
        config_extra(cfg)
    yaml.safe_dump(cfg, open(wd / "config.yaml", "w"))
    os.chdir(wd)
    return wd


def assets(name):
    yaml.safe_dump(RUBRIC, open(f"data/rubrics/{name}_rubric.yaml", "w"))
    Path(f"data/solutions/{name}-SOLUTION.tex").write_text(SOLUTION)


def patch_grader(score=lambda student_id, label: None, fail=frozenset(), inject=frozenset()):
    """Full marks unless score() returns a number; (name, label) in fail -> grading failure."""
    from lata import grader as g
    calls = []

    def fake(self, problem_label, student_text, solution_text, rubric_items, student_id, use_cache=True, **kw):
        calls.append((student_id, problem_label))
        if any(n in student_id for n, l in fail if l == problem_label):
            return []
        dd = self.debug_dir / student_id / problem_label.replace(" ", "_")
        dd.mkdir(parents=True, exist_ok=True)
        if any(n in student_id for n in inject):
            (dd / "SECURITY_FLAG.txt").write_text("flag")
        items = []
        for sec in rubric_items:
            for it in sec['items']:
                s = score(student_id, problem_label)
                items.append(g.GradeItem(problem_group=sec['title'], criterion_name=it['name'],
                                         score=it['points'] if s is None else s, max_score=it['points'],
                                         audit_reasoning="ok", student_hint="Good job"))
        return items
    g.LatexGrader.grade_chunk = fake
    return calls


class FakeSMTP:
    def __init__(self):
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)

    def noop(self):
        return (250, b"ok")

    def quit(self):
        pass


def patch_emailer():
    from lata import emailer as e
    smtp = FakeSMTP()

    def fake_auth(self, username=None, password=None):
        self.smtp_connection = smtp
        self.from_email = "instructor@oregonstate.edu"
    e.FeedbackEmailer.authenticate = fake_auth
    return smtp
