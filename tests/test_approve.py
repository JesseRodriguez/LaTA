"""Held reports: `held` lists them, `approve` releases one after review."""
import io, json, contextlib
from datetime import datetime
from pathlib import Path
import yaml
import fx
from fx import check
import fakegs
from fakegs import FakeGS

def cfg_extra(c):
    c['assignments'] = [{'name': 'HW3', 'rubric_file': 'HW3_rubric.yaml', 'solution_file': 'data/solutions/HW3-SOLUTION.tex',
                         'due_date': '2026-10-01 08:00:00', 'gradescope': {'tex': 10, 'pdf': 20}}]
    c['watch'] = {'source': 'gradescope', 'gradescope': {'course': 1}, 'send_notices': False}
fx.workdir(cfg_extra); fx.assets("HW3")
fx.patch_grader(inject={"Lee"})                     # "possible prompt injection" for Kim Lee and Lee Park
smtp = fx.patch_emailer()
gs = FakeGS(); fakegs.install(gs)
import lata.watch as W
from lata.pipeline import GradingOptions
W.utcnow = lambda: datetime(2026, 10, 1, 12, 0)
gs.tex = {s: (100 + int(s), "2026-09-30 10:00:00") for s in ("1", "3", "5")}
gs.pdf = {s: (200 + int(s), "2026-09-30 10:05:00") for s in ("1", "3", "5")}
w = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w.authenticate(); w.run_pass()
entered = lambda: {q for q, r, p in gs.saves}
def out(f):
    """What f() prints."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        f()
    return buf.getvalue()

check(entered() == {2010}, "held reports (possible injection) are not entered on Gradescope")
listing = out(lambda: W.handle_command(w, "held"))
check("Kim Lee: possible prompt injection" in listing and "Lee Park" in listing, "`held` lists them with the reason")
check(W.handle_command(w, "approve Lee") is False, "`approve Lee` matches two students: nothing approved")
check(W.handle_command(w, "approve kim") is True, "`approve kim` approves Kim Lee and asks for a check now")
w.run_pass()
check(2030 in entered() and 2050 not in entered(), "on that check Kim's grade goes on Gradescope; Lee Park stays held")

gs.published = True; gs.sketch_graded |= {"1", "3", "5"}
W.handle_command(w, "send")
sent = sorted(m['To'] for m in smtp.sent if list(m.iter_attachments()))
check(sent == ["doe1@oregonstate.edu", "lee3@oregonstate.edu"], f"`send` releases Jane and the approved Kim, not Lee Park ({sent})")

state = Path("data/output/HW3_Feedback/watch_state.json"); led = json.loads(state.read_text())
led["students"]["5"]["report"] = None; state.write_text(json.dumps(led))
w2 = W.Watcher(yaml.safe_load(open("config.yaml")), "config.yaml", GradingOptions()); w2.authenticate()
msg = out(lambda: W.handle_command(w2, "approve Lee Park"))
check("has no report to send" in msg, "approving a held student without a report is refused (regrade instead)")
fx.done()
