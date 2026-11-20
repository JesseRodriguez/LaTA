"""`regrade <name>` for any graded student, looked up across the watched assignments."""
import io, json, contextlib
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check
import fakegs
from fakegs import FakeGS

def cfg_extra(c):
    c['assignments'] = [{'name': n, 'rubric_file': f'{n}_rubric.yaml', 'solution_file': f'data/solutions/{n}-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00', 'gradescope': {'tex': 10, 'pdf': 20}} for n in ("HW3", "HW4")]
    c['watch'] = {'source': 'gradescope', 'gradescope': {'course': 1}, 'send_notices': False}
fx.workdir(cfg_extra); fx.assets("HW3"); fx.assets("HW4")
scores = {"value": None}
fx.patch_grader(score=lambda sid, label: scores["value"] if "Jane" in sid else None)
smtp = fx.patch_emailer()
gs = FakeGS(); fakegs.install(gs)   # both assignments share this fake; HW3 enters Jane's grade, HW4 sees it as a match
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 12, 0)
gs.tex = {"1": (101, "2026-09-30 10:00:00"), "3": (103, "2026-09-30 10:00:00")}
gs.pdf = {"1": (201, "2026-09-30 10:05:00"), "3": (203, "2026-09-30 10:05:00")}
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
gs.published = True; gs.sketch_graded |= {"1", "3"}
W.handle_command(w, "send")
state = lambda n: json.loads(Path(f"data/output/{n}_Feedback/watch_state.json").read_text())["students"]
def say(cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = W.handle_command(w, cmd)
    return r, buf.getvalue()
check(state("HW3")["1"].get("feedback_sent_at") and state("HW4")["1"].get("feedback_sent_at"), "Jane graded and sent on both assignments")

r, text = say("regrade jane")
check(r is False and "1) HW3" in text and "2) HW4" in text and "feedback sent" in text, "`regrade jane`: two assignments match, listed with their state")
r, text = say("1")
check(r is True and "HW3: Jane Doe will be regraded now -- every problem afresh" in text and "sent again when released" in text,
      "answering 1 queues HW3, from scratch (she was not held), and says her feedback goes out again")
check(state("HW4")["1"].get("graded") and not state("HW4")["1"].get("regrade"), "HW4 untouched")

scores["value"] = 1                                  # the regrade comes out lower: 4/10
n_saves, n_mail = len(gs.saves), len(smtp.sent)
w.run_pass()
st = state("HW3")["1"]
check(st.get("graded") and not st.get("regrade") and not st.get("feedback_sent_at") and st.get("resend"), "regraded; waiting to be sent again")
check(gs.saves[n_saves:] and gs.saves[-1][2] == -6.0, f"the new score replaced LaTA's earlier entry on Gradescope ({gs.saves[n_saves:]})")
W.handle_command(w, "send")
resent = [m['To'] for m in smtp.sent[n_mail:] if list(m.iter_attachments())]
check(resent == ["doe1@oregonstate.edu"], f"`send` re-sends Jane's new report, and only hers ({resent})")
check(state("HW3")["1"].get("feedback_sent_at") and not state("HW3")["1"].get("resend"), "recorded as sent again")

r, text = say("regrade jane hw4")
check(r is True and "HW4: Jane Doe" in text, "naming the assignment skips the question")
r, text = say("regrade nobody")
check(r is False and "No student matching" in text, "unknown name: says so")
r, text = say("regrade lee"); r2, text2 = say("")
check("2) HW4" in text and r2 is False and "cancelled" in text2, "Enter at the question cancels")
fx.done()
