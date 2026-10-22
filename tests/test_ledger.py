"""The watcher's rules (lata.ledger.decide) across submission timelines."""
import tempfile, warnings
from datetime import datetime
from pathlib import Path
import fx
from fx import check
from lata.ledger import Ledger, Policy, StudentSnapshot, Submission, decide, read_snapshot, parse_ts, gradescope_ts
from lata.inbox import prepare_from_inbox
warnings.filterwarnings("ignore", category=DeprecationWarning)

T = lambda s: datetime.strptime(f"2026-{s}", "%Y-%m-%d %H:%M")
DUE, CDUE = T("10-01 08:00"), T("10-08 08:00")
def snap(*students): return {s.key: s for s in students}
def stu(key, tex=None, pdf=None, has_tex=True, group=False):
    return StudentSnapshot(key=key, name=f"Student {key}", email=f"s{key}@oregonstate.edu", sid=key,
                           tex=Submission(f"t{key}", T(tex), has_tex) if tex else None,
                           pdf=Submission(f"p{key}:{pdf}", T(pdf)) if pdf else None, group_member=group)
def L(): return Ledger(Path(tempfile.mkdtemp()))
def run(l, s, now, due=DUE, **kw):
    a, w = decide(l, s, Policy(due=due, **kw), T(now))
    return [(x.kind, x.key, x.detail, x.created_at) for x in a], {x.key: x.reason for x in w}

print("main assignment")
l = L(); s = snap(stu("1", "09-30 20:00", "09-30 20:05"))
a, w = run(l, s, "10-01 07:00"); check(a == [] and "deadline" in w["1"], "on time, before deadline: wait")
a, w = run(l, s, "10-01 08:00"); check(a == [("grade", "1", "", T("09-30 20:05"))], "at deadline: grade, charged the later of .tex/PDF")
s = snap(stu("2", "10-01 09:00", "10-01 09:10"))
a, w = run(l, s, "10-01 09:20"); check(a == [] and "settling" in w["2"], "late pair, 10 min old: settling")
a, w = run(l, s, "10-01 09:25"); check(a[0][:2] == ("grade", "2") and a[0][3] == T("10-01 09:10"), "late pair after 15 min quiet: grade")
s = snap(stu("3", "10-01 07:30"))
a, w = run(l, s, "10-01 08:10"); check(a == [] and "notice due" in w["3"], ".tex 40 min old at deadline: no notice yet (1h rule)")
a, w = run(l, s, "10-01 08:31"); check(a == [("notice", "3", "missing_pdf", None)], ".tex 1h old, past deadline: missing PDF notice")
l.get("3").setdefault("notices", {})["missing_pdf"] = "x"
a, w = run(l, s, "10-01 12:00"); check(a == [] and "notified" in w["3"], "notice sent once only")
s = snap(stu("3", "10-01 07:30", "10-01 15:00"))
a, w = run(l, s, "10-01 15:20"); check(a[0][:2] == ("grade", "3") and a[0][3] == T("10-01 15:00"), "PDF arrives: graded, charged the PDF time")
a, w = run(l, snap(stu("4", "09-30 10:00")), "09-30 18:00"); check(a == [], ".tex without PDF before the deadline: nothing")
a, w = run(l, snap(stu("5", None, "10-01 10:00")), "10-01 11:00"); check(a == [("notice", "5", "missing_tex", None)], "PDF, no .tex: missing .tex notice")
a, w = run(l, snap(stu("6", "10-01 07:00", "10-01 07:00", has_tex=False)), "10-01 09:00"); check(a == [("notice", "6", "missing_tex", None)], ".tex submission without a .tex file + PDF: .tex notice")
a, w = run(l, snap(stu("7", "10-01 07:00", None, has_tex=False)), "10-01 09:00"); check(a[0][0] == "flag", "no .tex file and no PDF: flagged")
a, w = run(l, snap(stu("8", "09-30 10:00", "09-30 10:00")), "10-01 09:00", extensions={"Student 8": 24})
check(a and a[0][:2] == ("grade", "8"), "student with a 24h extension who has submitted: graded at the assignment's due date")
a, w = run(l, snap(stu("8", "10-01 10:00")), "10-01 12:00", extensions={"Student 8": 24})
check(a == [] and "notice due" in w["8"], "...but their missing-PDF notice waits for their own (extended) deadline")
a, w = run(l, snap(stu("8", "10-01 10:00")), "10-02 08:01", extensions={"Student 8": 24})
check(a == [("notice", "8", "missing_pdf", None)], "notice once their extension is over")
a, w = run(l, snap(stu("9", "09-30 10:00", "09-30 10:00", group=True)), "10-01 09:00"); check(a == [] and w == {}, "group member: graded with the group")

print("after grading")
l = L(); first = stu("1", "09-30 20:00", "09-30 20:05")
l.mark_graded("1", first, T("09-30 20:05"), first.pdf, T("10-01 08:00"), None, [])
a, w = run(l, snap(stu("1", "09-30 20:00", "10-03 12:00")), "10-03 13:00"); check(a == [], "graded, later (corrections) PDF: nothing")
check(l.first_pass("1") == ("graded", T("09-30 20:05")), "first-pass time frozen at the graded PDF")
s = snap(stu("1", "10-02 10:00", "09-30 20:05"))
a, w = run(l, s, "10-02 11:00"); check(len(a) == 1 and a[0][0] == "flag" and "Not regraded" in a[0][2], "graded, newer .tex: flagged, not regraded")
a, w = run(l, s, "10-02 11:00", extensions={"Student 1": 48})
check("within their extension" in a[0][2], "resubmission inside the student's extension: the flag says so")
l.get("1").setdefault("flags", {})["resubmission:t1:2026-10-02 10:00:00"] = {}
a, w = run(l, s, "10-02 12:00"); check(a == [], "resubmission flagged once")

print("corrections")
base = L(); g = stu("1", "09-30 20:00", "09-30 20:05"); base.mark_graded("1", g, T("09-30 20:05"), g.pdf, T("10-01 08:00"), None, [])
base_snap = snap(g, stu("2", "10-04 10:00", "10-04 10:05"))
def fp(key):
    status, t = base.first_pass(key)
    if status == "graded": return status, t
    b = base_snap.get(key)
    return ("pending", None) if (b and b.tex and b.tex.has_tex) else ("none", None)
def crun(l, s, now): return run(l, s, now, due=CDUE, corrections=True, first_pass=fp)
c = L()
a, w = crun(c, snap(stu("1", "10-05 10:00", "10-05 10:05")), "10-08 09:00"); check(a == [("grade", "1", "", T("10-05 10:05"))], "corrections .tex + new PDF: graded")
s = snap(stu("1", "10-05 10:00", "09-30 20:05"))
a, w = crun(c, s, "10-08 07:00"); check(a == [] and "deadline" in w["1"], "corrections, no new PDF, before deadline: waits")
a, w = crun(c, s, "10-08 09:00"); check(a == [("grade", "1", "", T("10-05 10:00"))], "corrections, no new PDF: graded at deadline (incomplete)")
c.mark_graded("1", s["1"], T("10-05 10:00"), None, T("10-08 09:00"), None, [])
s = snap(stu("1", "10-05 10:00", "10-09 12:00"))
a, w = crun(c, s, "10-09 13:00"); check(len(a) == 1 and "Decide whether to apply" in a[0][2], "PDF after that feedback: flagged")
c = L()
a, w = crun(c, snap(stu("1", None, "10-05 10:05")), "10-08 09:00"); check(a == [("notice", "1", "missing_tex_corrections", None)], "new PDF, no corrections .tex: notice")
a, w = crun(c, snap(stu("1", None, "09-30 20:05")), "10-08 09:00"); check(a == [] and w == {}, "skips corrections: left alone")
a, w = crun(c, snap(stu("2", "10-05 10:00", "10-05 11:00")), "10-08 09:00"); check(a == [] and "first pass not graded" in w["2"], "corrections before the (late) first pass is graded: waits")
a, w = crun(c, snap(stu("3", "10-05 10:00", "10-05 11:00")), "10-08 09:00"); check(a == [("grade", "3", "", T("10-05 10:00"))], "no first pass at all: graded (no-original report)")
a, w = run(L(), snap(stu("2", "10-04 10:00", "10-04 10:05")), "10-04 10:30"); check(a == [("grade", "2", "", T("10-04 10:05"))], "late first pass after corrections opened: graded on the main assignment")

print("snapshots")
d = Path(tempfile.mkdtemp())
fx.tex_zip(d / "inbox/t.zip", {1: ("Jane Doe", "1", "2026-10-01 06:00:00"), 2: ("Student C", "3", "2026-10-01 06:00:00")}, tex_ok={1})
fx.pdf_zip(d / "inbox/p.zip", {11: ("Jane Doe", "1", "2026-10-01 06:30:00"), 12: ("John Smith", "2", "2026-10-01 07:00:00")})
prepare_from_inbox(d / "inbox", d / "export", quiet=True)
sn = read_snapshot(d / "export")
check(sn["1"].tex.has_tex and sn["1"].pdf.created_at == T("10-01 06:30"), ".tex + PDF read with times")
check(not sn["3"].tex.has_tex and sn["3"].pdf is None and sn["2"].tex is None, "no-.tex-file and PDF-only students")
fx.tex_zip(d / "inbox/t2.zip", {7: ("Jane Doe", "1", "2026-10-02 06:00:00")})
prepare_from_inbox(d / "inbox", d / "export", quiet=True)
check(read_snapshot(d / "export")["1"].tex.id == "submission_7", "re-export replaces a student's older submission")
check(parse_ts("2026-10-01 06:30:00.123456000 Z") == T("10-01 06:30") == parse_ts("2026-10-01T01:30:00-05:00")
      == parse_ts(gradescope_ts(T("10-01 06:30"))), "timestamp parsing, offsets, round trip")
fx.done()
