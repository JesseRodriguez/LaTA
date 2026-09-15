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
Multi-slot grading: a work queue of (student, problem) jobs and a live progress monitor.

Grading one problem is one long LLM request, and those requests are nearly all of the
wall-clock time. With concurrency N, up to N of them run at once against a server that
has N slots (Ollama: OLLAMA_NUM_PARALLEL >= N; llama.cpp: --parallel >= N). Requests
beyond the server's slot count simply queue, so N higher than the server allows buys
nothing.

Only LLM calls run on worker threads. Everything that touches shared files -- report
rendering, pdflatex, grading_metadata.yaml -- happens on the main thread when a student's
last problem finishes, so none of it needs locking.
"""

import io
import os
import sys
import time
import shutil
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List, Optional


@dataclass
class ProblemJob:
    """One LLM grading request: a single problem of a single student."""
    student_index: int
    problem_index: int
    problem_label: str
    student_text: str
    solution_text: str
    rubric_items: list


@dataclass
class StudentPlan:
    """Everything needed to grade one student, prepared before grading starts."""
    student: object                     # ingest.StudentSubmission
    is_regrade: bool
    segmentation_warning: bool
    jobs: List[ProblemJob] = field(default_factory=list)


class GradingCancelled(Exception):
    """Raised inside a worker when the run is being stopped (Ctrl-C)."""


# -----------------------------------------------------------------------------
# Progress monitor
# -----------------------------------------------------------------------------

def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _short_name(name: str, width: int) -> str:
    return name if len(name) <= width else name[: width - 1] + "…"


@dataclass
class _Slot:
    student: str = ""
    problem: str = ""
    phase: str = "idle"          # idle | waiting | thinking | answering | parsing
    started: float = 0.0
    tokens: int = 0
    first_token_at: float = 0.0
    thinking_tail: str = ""


class _LineCapture(io.TextIOBase):
    """Stands in for sys.stdout while the dashboard owns the terminal.

    Complete lines go to the monitor's event feed and to the run log, so nothing the
    grader or reporter prints is lost -- it is just shown in the "Recent" panel instead
    of scrolling the dashboard away. Partial lines are buffered per thread, so text
    written in pieces by one slot never gets spliced into another slot's line.
    """

    def __init__(self, monitor: "ProgressMonitor"):
        self.monitor = monitor
        self._bufs = {}
        self._lock = threading.Lock()

    def writable(self):
        return True

    def write(self, s):
        tid = threading.get_ident()
        lines = []
        with self._lock:
            buf = self._bufs.get(tid, "") + s
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                lines.append(line)
            self._bufs[tid] = buf
        for line in lines:
            self.monitor.event(line)
        return len(s)

    def flush(self):
        pass

    def drain(self):
        with self._lock:
            leftovers, self._bufs = [b for b in self._bufs.values() if b], {}
        for line in leftovers:
            self.monitor.event(line)


class _TeeCapture(_LineCapture):
    """Plain-mode stdout: lines still go to the terminal, and also to the run log and the
    monitor's counters (score fixes, security flags), one whole line at a time."""

    def __init__(self, monitor: "ProgressMonitor", real):
        super().__init__(monitor)
        self.real = real

    def write(self, s):
        tid = threading.get_ident()
        lines = []
        with self._lock:
            buf = self._bufs.get(tid, "") + s
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                lines.append(line)
            self._bufs[tid] = buf
            if lines:
                self.real.write("".join(l + "\n" for l in lines))
                self.real.flush()
        for line in lines:
            self.monitor.event(line)
        return len(s)

    def flush(self):
        self.real.flush()

    def isatty(self):
        return False


class ProgressMonitor:
    """Thread-safe progress state, plus a live terminal dashboard or periodic status lines.

    Workers report through begin/phase/token/end; the main thread reports finished
    students through student_done. Slots are assigned to worker threads on first use.
    """

    def __init__(self, total_students: int, total_problems: int, concurrency: int,
                 title: str, log_path, watch_slot: int = 1, dashboard: bool = True,
                 status_interval: float = 60.0):
        self.total_students = total_students
        self.total_problems = total_problems
        self.concurrency = concurrency
        self.title = title
        self.log_path = log_path
        self.watch_slot = watch_slot
        self.dashboard = dashboard
        self.status_interval = status_interval

        self._lock = threading.RLock()
        self._slots = [_Slot() for _ in range(concurrency)]
        self._thread_slot = {}
        self._events = deque(maxlen=200)
        self._token_times = deque()          # (timestamp, n) for a rolling tok/s
        self._job_seconds = deque(maxlen=50)

        self.started = time.time()
        self.problems_done = 0
        self.problems_failed = 0
        self.problems_cancelled = 0
        self.students_done = 0
        self.students_failed = 0
        self.validation_fixes = 0
        self.security_flags = 0
        self.cached = 0

        self._stop = threading.Event()
        self._render_thread = None
        self._real_stdout = None
        self._capture = None
        self._log_file = None

    # -- slot bookkeeping (called from worker threads) ------------------------

    def _slot(self) -> _Slot:
        tid = threading.get_ident()
        with self._lock:
            if tid not in self._thread_slot:
                used = set(self._thread_slot.values())
                free = next((i for i in range(self.concurrency) if i not in used), len(used) % self.concurrency)
                self._thread_slot[tid] = free
            return self._slots[self._thread_slot[tid]]

    def slot_number(self) -> int:
        tid = threading.get_ident()
        with self._lock:
            self._slot()
            return self._thread_slot[tid] + 1

    def begin(self, student: str, problem: str):
        s = self._slot()
        with self._lock:
            s.student, s.problem, s.phase = student, problem, "waiting"
            s.started, s.tokens, s.first_token_at, s.thinking_tail = time.time(), 0, 0.0, ""

    def phase(self, phase: str):
        s = self._slot()
        with self._lock:
            s.phase = phase

    def token(self, kind: str, text: str):
        s = self._slot()
        now = time.time()
        with self._lock:
            if not s.first_token_at:
                s.first_token_at = now
            s.tokens += 1
            s.phase = "thinking" if kind == "thinking" else "answering"
            if kind == "thinking":
                s.thinking_tail = (s.thinking_tail + text)[-4000:]
            self._token_times.append((now, 1))

    def end(self, ok: bool, cached: bool = False, cancelled: bool = False):
        s = self._slot()
        with self._lock:
            if cancelled:
                # stopped by Ctrl-C mid-request: neither graded nor a failure
                self.problems_cancelled += 1
            else:
                if cached:
                    self.cached += 1
                elif s.started:
                    self._job_seconds.append(time.time() - s.started)
                if ok:
                    self.problems_done += 1
                else:
                    self.problems_failed += 1
            s.phase, s.student, s.problem, s.tokens, s.thinking_tail = "idle", "", "", 0, ""

    # -- main-thread reporting -------------------------------------------------

    def student_done(self, ok: bool):
        with self._lock:
            if ok:
                self.students_done += 1
            else:
                self.students_failed += 1

    def event(self, line: str):
        line = line.rstrip()
        if not line.strip():
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            if "Score validation" in line:
                try:
                    self.validation_fixes += int(line.split(":", 1)[1].split("fix")[0].strip())
                except (IndexError, ValueError):
                    pass
            if "SECURITY" in line:
                self.security_flags += 1
            self._events.append(f"{stamp}  {line.strip()}")
        if self._log_file:
            try:
                self._log_file.write(f"{stamp}  {line}\n")
                self._log_file.flush()
            except ValueError:
                pass

    # -- derived numbers ---------------------------------------------------------

    def _tok_per_sec(self, window: float = 30.0) -> float:
        now = time.time()
        with self._lock:
            while self._token_times and now - self._token_times[0][0] > window:
                self._token_times.popleft()
            n = sum(k for _, k in self._token_times)
            span = min(window, now - self.started)
        return n / span if span > 1 else 0.0

    def _eta(self):
        with self._lock:
            finished = self.problems_done + self.problems_failed
            remaining = self.total_problems - finished
            fresh = finished - self.cached
        elapsed = time.time() - self.started
        if fresh < max(2, self.concurrency) or remaining <= 0:
            return None, None
        rate = fresh / elapsed                      # problems per second, actually achieved
        secs = remaining / rate if rate > 0 else None
        return secs, (rate * 3600)

    # -- lifecycle ---------------------------------------------------------------

    def start(self):
        self._log_file = open(self.log_path, "a", encoding="utf-8")
        self._log_file.write(f"\n===== {datetime.now().isoformat(timespec='seconds')}  {self.title} "
                             f"({self.concurrency} slots) =====\n")
        self._real_stdout = sys.stdout
        if self.dashboard:
            self._capture = _LineCapture(self)
            sys.stdout = self._capture
            self._real_stdout.write("\x1b[?1049h\x1b[?25l")   # alternate screen, hide cursor
            self._real_stdout.flush()
        else:
            self._capture = _TeeCapture(self, self._real_stdout)
            sys.stdout = self._capture
        self._render_thread = threading.Thread(target=self._loop, daemon=True)
        self._render_thread.start()

    def stop(self):
        self._stop.set()
        if self._render_thread:
            self._render_thread.join(timeout=5)
        if self._real_stdout is not None:
            try:
                self._capture.drain()
            finally:
                sys.stdout = self._real_stdout
                if self.dashboard:
                    self._real_stdout.write("\x1b[?25h\x1b[?1049l")   # show cursor, leave alternate screen
                    self._real_stdout.flush()
                self._real_stdout = None
        print(self.summary())
        if self._log_file:
            self._log_file.write(self.summary() + "\n")
            self._log_file.close()
            self._log_file = None

    def _loop(self):
        last_status = 0.0
        while not self._stop.wait(0.5 if self.dashboard else 1.0):
            if self.dashboard:
                self._render()
            elif time.time() - last_status >= self.status_interval:
                last_status = time.time()
                print(self.status_line(), flush=True)

    # -- rendering -----------------------------------------------------------------

    def status_line(self) -> str:
        eta, per_hr = self._eta()
        with self._lock:
            busy = sum(1 for s in self._slots if s.phase != "idle")
            parts = [
                f"[{_fmt_duration(time.time() - self.started)}]",
                f"students {self.students_done}/{self.total_students}",
                f"problems {self.problems_done + self.problems_failed}/{self.total_problems}",
                f"slots busy {busy}/{self.concurrency}",
                f"{self._tok_per_sec():.1f} tok/s",
            ]
            if per_hr:
                parts.append(f"{per_hr:.0f} problems/hr")
            if eta:
                parts.append(f"ETA {_fmt_duration(eta)}")
            if self.problems_failed:
                parts.append(f"failed {self.problems_failed}")
        return "   📊 " + "  ".join(parts)

    def summary(self) -> str:
        elapsed = time.time() - self.started
        with self._lock:
            lines = [
                "",
                f"📊 Grading run finished in {_fmt_duration(elapsed)} ({self.concurrency} slots)",
                f"   Students reported: {self.students_done}/{self.total_students}"
                + (f"  (report failures: {self.students_failed})" if self.students_failed else ""),
                f"   Problems graded:   {self.problems_done}/{self.total_problems}"
                + (f"  (failed: {self.problems_failed})" if self.problems_failed else "")
                + (f"  (stopped mid-request: {self.problems_cancelled})" if self.problems_cancelled else "")
                + (f"  (reused from a previous run: {self.cached})" if self.cached else ""),
            ]
            if self.validation_fixes:
                lines.append(f"   Score validation fixes: {self.validation_fixes}")
            if self.security_flags:
                lines.append(f"   🚨 Security flags: {self.security_flags} (see run log)")
            lines.append(f"   Run log: {self.log_path}")
        return "\n".join(lines)

    def _render(self):
        cols, rows = shutil.get_terminal_size((120, 40))
        cols = max(60, cols)
        rule = "─" * cols
        now = time.time()
        eta, per_hr = self._eta()

        with self._lock:
            finished = self.problems_done + self.problems_failed
            out = []
            out.append(_short_name(f" LaTA  ·  {self.title}  ·  {self.concurrency} slots  ·  "
                                   f"elapsed {_fmt_duration(now - self.started)}", cols))
            pct = (finished / self.total_problems * 100) if self.total_problems else 100.0
            bar_w = max(10, min(40, cols - 70))
            filled = int(bar_w * pct / 100)
            out.append(f" [{'█' * filled}{'░' * (bar_w - filled)}] {pct:5.1f}%   "
                       f"students {self.students_done}/{self.total_students}   "
                       f"problems {finished}/{self.total_problems}")
            stats = [f"{self._tok_per_sec():.1f} tok/s"]
            stats.append(f"{per_hr:.0f} problems/hr" if per_hr else "problems/hr --")
            if eta:
                done_at = (datetime.now() + timedelta(seconds=eta)).strftime("%a %H:%M")
                stats.append(f"ETA {_fmt_duration(eta)} (≈ {done_at})")
            else:
                stats.append("ETA -- (measuring)")
            flags = []
            if self.problems_failed:
                flags.append(f"❌ {self.problems_failed} failed")
            if self.validation_fixes:
                flags.append(f"🔍 {self.validation_fixes} score fixes")
            if self.security_flags:
                flags.append(f"🚨 {self.security_flags} security")
            if self.cached:
                flags.append(f"♻️  {self.cached} reused")
            out.append(" " + "   ".join(stats + flags))
            out.append(rule)

            name_w = max(12, min(28, cols - 70))
            for i, s in enumerate(self._slots, 1):
                if s.phase == "idle":
                    out.append(f" slot {i:<2} ·  idle")
                    continue
                dt = now - s.started
                gen_dt = now - s.first_token_at if s.first_token_at else 0
                rate = f"{s.tokens / gen_dt:5.1f} tok/s" if gen_dt > 2 else "   -- tok/s"
                note = "  (prefill / queued)" if s.phase == "waiting" else ""
                out.append(f" slot {i:<2} {_short_name(s.student, name_w):<{name_w}}  "
                           f"{_short_name(s.problem, 12):<12} {s.phase:<9} {_fmt_duration(dt):>7}  "
                           f"{s.tokens:>6,} tok  {rate}{note}")
            out.append(rule)

            # live thinking for the watched slot (fall back to any busy slot)
            watch = self.watch_slot - 1 if 0 < self.watch_slot <= self.concurrency else 0
            if self._slots[watch].phase == "idle":
                busy = [j for j, s in enumerate(self._slots) if s.phase != "idle"]
                watch = busy[0] if busy else watch
            ws = self._slots[watch]
            head = f" Live thinking · slot {watch + 1}"
            if ws.phase != "idle":
                head += f" · {ws.student} · {ws.problem}"
            out.append(_short_name(head + "   (--watch-slot N to follow another slot)", cols))

            fixed = len(out) + 5                      # rules, recent header, footer
            recent_n = max(3, min(8, (rows - fixed) // 3))
            think_n = max(3, rows - fixed - recent_n)
            tail_lines = []
            for para in ws.thinking_tail.splitlines() or [""]:
                while len(para) > cols - 3:
                    tail_lines.append(para[: cols - 3])
                    para = para[cols - 3:]
                tail_lines.append(para)
            tail_lines = tail_lines[-think_n:]
            out.extend("   " + t for t in tail_lines)
            out.extend([""] * (think_n - len(tail_lines)))
            out.append(rule)

            out.append(" Recent")
            events = list(self._events)[-recent_n:]
            out.extend(" " + _short_name(e, cols - 2) for e in events)
            out.extend([""] * (recent_n - len(events)))
            out.append(_short_name(f" Log: {self.log_path}   ·   Ctrl-C stops the run; "
                                   f"finished problems are kept and reused on restart", cols))

        frame = "\x1b[H" + "\n".join(line[:cols].ljust(cols) for line in out[: rows - 1]) + "\x1b[J"
        try:
            self._real_stdout.write(frame)
            self._real_stdout.flush()
        except (ValueError, OSError):
            pass


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------

def run_jobs(plans: List[StudentPlan], grade_job: Callable, finish_student: Callable,
             concurrency: int, monitor: Optional[ProgressMonitor], stop_event: threading.Event):
    """
    Grade every job in `plans` with up to `concurrency` requests in flight.

    grade_job(plan, job) -> list of GradeItem runs on a worker thread.
    finish_student(plan, items_in_rubric_order) runs on the calling (main) thread as soon
    as all of that student's problems have come back, so reports appear progressively
    and a stopped run keeps every student it finished.

    Jobs are queued in student order, so with N slots students tend to finish one after
    another instead of all at the very end.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pending = {}          # student_index -> number of jobs outstanding
    results = {}          # student_index -> {problem_index: items}
    by_index = {}
    for plan in plans:
        idx = plan.jobs[0].student_index if plan.jobs else None
        if idx is None:
            continue
        by_index[idx] = plan
        pending[idx] = len(plan.jobs)
        results[idx] = {}

    def collect(job, items):
        results[job.student_index][job.problem_index] = items
        pending[job.student_index] -= 1
        if pending[job.student_index] == 0:
            plan = by_index[job.student_index]
            ordered = []
            for p in sorted(results[job.student_index]):
                ordered.extend(results[job.student_index][p])
            finish_student(plan, ordered)

    if concurrency <= 1:
        # Serial path: identical ordering and console behaviour to the original loop.
        for plan in plans:
            for job in plan.jobs:
                if stop_event.is_set():
                    raise KeyboardInterrupt
                collect(job, grade_job(plan, job))
        return

    executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="lata-slot")
    futures = {}
    try:
        for plan in plans:
            for job in plan.jobs:
                futures[executor.submit(grade_job, plan, job)] = job
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                items = fut.result()
            except GradingCancelled:
                continue
            collect(job, items)
    except KeyboardInterrupt:
        stop_event.set()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
