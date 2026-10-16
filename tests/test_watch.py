"""A simulated week of the watcher with the inbox source: grading, notices, flags, corrections."""
import json
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check

def cfg_extra(c):
    c['grading']['late_penalties'].update(enabled=True, penalty_per_day=0.2)
    c['assignments'] = [
        {'name': 'HW1', 'rubric_file': 'HW1_rubric.yaml', 'solution_file': 'data/solutions/HW1-SOLUTION.tex', 'due_date': '2026-10-01 08:00:00'},
        {'name': 'HW1_CORRECTIONS', 'rubric_file': 'HW1_CORRECTIONS_rubric.yaml', 'solution_file': 'data/solutions/HW1_CORRECTIONS-SOLUTION.tex', 'due_date': '2026-10-08 08:00:00'}]
    c['watch'] = {'auto_send_feedback': True, 'notify_email': 'instructor@oregonstate.edu'}
fx.workdir(cfg_extra); fx.assets("HW1"); fx.assets("HW1_CORRECTIONS")
fx.patch_grader(fail={("Student E", "Problem 1")})
smtp = fx.patch_emailer()
import lata.watch as W
from lata.pipeline import GradingOptions
CLOCK = [None]; W.utcnow = lambda: CLOCK[0]
watcher = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); watcher.authenticate()

NAMES = {"1": "Jane Doe", "2": "John Smith", "3": "Student C", "4": "Student D", "5": "Student E", "6": "Student F"}
tex, pdf, ctex, n = {}, {}, {}, [0]
def export(folder, current, kind):
    n[0] += 1
    subs = {f"{sid}{i}": (NAMES[sid], sid, t) for sid, (i, t) in current.items()}
    (fx.tex_zip if kind == "tex" else fx.pdf_zip)(Path(f"data/input/inbox/{folder}/{kind}_{n[0]}.zip"), subs)
def step(now):
    CLOCK[0] = datetime.strptime(now, "%Y-%m-%d %H:%M"); before = len(smtp.sent)
    watcher.run_pass(); return smtp.sent[before:]
to = lambda msgs: sorted((m['To'], bool(list(m.iter_attachments()))) for m in msgs)
body = lambda m: m.get_body().get_content()

tex["1"] = (1, "2026-09-30 10:00:00"); tex["2"] = (1, "2026-09-30 11:00:00"); tex["5"] = (1, "2026-09-30 11:00:00")
pdf["1"] = (1, "2026-09-30 10:05:00"); pdf["3"] = (1, "2026-09-30 11:30:00"); pdf["5"] = (1, "2026-09-30 11:10:00")
export("HW1", tex, "tex"); export("HW1", pdf, "pdf")
check(step("2026-09-30 12:00") == [], "nothing before the deadline")
r = step("2026-10-01 08:05")
check(to(r) == sorted([("doe1@oregonstate.edu", True), ("smith2@oregonstate.edu", False), ("c3@oregonstate.edu", False), ("instructor@oregonstate.edu", False)]),
      "deadline: Jane's feedback, John's PDF notice, C's .tex notice, instructor told E is held")
check(any("late penalty accruing" in m['Subject'] for m in r), "John's notice: late penalty accruing")
pdf["2"] = (2, "2026-10-01 15:00:00"); export("HW1", pdf, "pdf")
check(to(step("2026-10-01 15:30")) == [("smith2@oregonstate.edu", True)], "John graded and sent when his PDF arrives")
tex["1"] = (2, "2026-10-02 11:00:00"); export("HW1", tex, "tex")
r = step("2026-10-02 12:00"); check(to(r) == [("instructor@oregonstate.edu", False)] and "Not regraded" in body(r[0]), "Jane's resubmission flagged, not regraded")
tex["4"] = (1, "2026-10-03 10:00:00"); pdf["4"] = (1, "2026-10-03 10:05:00"); export("HW1", tex, "tex"); export("HW1", pdf, "pdf")
check(to(step("2026-10-03 10:30")) == [("d4@oregonstate.edu", True)], "late first pass after corrections opened: graded and sent")
ctex["1"] = (1, "2026-10-05 10:00:00"); ctex["2"] = (1, "2026-10-05 10:00:00"); ctex["6"] = (1, "2026-10-05 10:00:00")
pdf["1"] = (3, "2026-10-05 10:30:00"); export("HW1_CORRECTIONS", ctex, "tex"); export("HW1", pdf, "pdf")
check(step("2026-10-05 12:00") == [], "corrections wait for their deadline")
r = step("2026-10-08 08:05")
check(to(r) == sorted([("doe1@oregonstate.edu", True), ("smith2@oregonstate.edu", True), ("f6@oregonstate.edu", True)]), "corrections deadline: Jane, John and F get feedback")
john = next(m for m in r if m['To'] == "smith2@oregonstate.edu"); jane = next(m for m in r if m['To'] == "doe1@oregonstate.edu")
check("NOT be applied" in body(john) and "final grade on this assignment" not in body(john) and "NOT be applied" not in body(jane),
      "John (no new PDF) told corrections grade won't apply; Jane's email normal")
check(step("2026-10-08 08:20") == [], "idempotent")
pdf["2"] = (3, "2026-10-09 12:00:00"); export("HW1", pdf, "pdf")
r = step("2026-10-09 13:00"); check(to(r) == [("instructor@oregonstate.edu", False)] and "Decide whether to apply" in body(r[0]), "John's late PDF flagged")
meta = yaml.safe_load(open("data/output/HW1_Feedback/grading_metadata.yaml"))
pen = {k: round(v.get('late_penalty', -1), 6) for k, v in meta.items()}
check(pen["Jane_Doe"] == 0 and pen["John_Smith"] == 0.2 and pen["Student_D"] == 0.6, f"late penalties: {pen}")
led = json.load(open("data/output/HW1_Feedback/watch_state.json"))["students"]
check(led["1"]["graded"]["effective"] == "2026-09-30 10:05:00", "Jane's first-pass time frozen despite her corrections PDF")
check(led["5"].get("held") and not led["5"].get("feedback_sent_at"), "Student E held, not sent")
fx.done()
