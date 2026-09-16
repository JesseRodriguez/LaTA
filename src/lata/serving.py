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
Name a model; LaTA finds it and serves it.

    system:
      grader:
        model: "qwen"            # enough to identify it

The name is matched against the models Ollama has pulled and the GGUF files on disk. An
Ollama model is used through Ollama. A GGUF is served by starting llama-server for the
run and stopping it afterwards, with the slot count matched to grading.concurrency and
per-architecture settings applied from the GGUF's own header.

The older explicit form still works and takes precedence, so existing configs are
unaffected:

    grader:
      backend: openai
      base_url: "http://localhost:8080/v1"
      model_name: "qwen38-next"
"""

import os
import re
import json
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Where GGUF files are looked for when system.model_dirs is not set.
DEFAULT_MODEL_DIRS = ["~/models", "~/gguf", "~/.cache/lm-studio/models"]
LLAMA_SERVER_CANDIDATES = ["llama-server", "~/llama.cpp/build/bin/llama-server",
                           "/usr/local/bin/llama-server", "/opt/homebrew/bin/llama-server"]

# Settings that depend on the model's architecture rather than the user's preferences.
# The architecture is read from the GGUF header, so it is right even when a file has been
# renamed. Anything the config sets explicitly wins over these.
ARCH_PROFILES = {
    # Qwen3.8-Flash-Next and relatives. Its template defaults to reasoning_effort xhigh,
    # where some problems think until they hit max_tokens and return nothing; low used
    # ~43% fewer tokens in testing. A server-side reasoning budget is NOT usable: when one
    # fires this model stops with an empty answer.
    "qwen4exp": {
        "chat_template_kwargs": {"reasoning_effort": "low"},
        "openai_options": {"temperature": 0.1, "top_p": 0.9, "presence_penalty": 0.2,
                           "max_tokens": 16384},
        "ctx_per_slot": 32768,
        "note": "reasoning_effort=low; no reasoning budget (it returns empty output)",
    },
    # DeepSeek V4-Flash: thinks at length and needs its thinking bounded, or it spends the
    # whole token budget reasoning and returns no answer.
    "deepseek_v4": {
        "openai_options": {"temperature": 0.1, "top_p": 0.9, "max_tokens": 32768},
        "ctx_per_slot": 40960,
        "server_args": ["--reasoning-budget", "28000",
                        "--reasoning-budget-message",
                        "Time to stop thinking. Give your final answer now based on the reasoning so far."],
        "note": "thinking capped at 28k tokens so it always produces an answer",
    },
}
DEFAULT_PROFILE = {
    "openai_options": {"temperature": 0.1, "top_p": 0.9, "max_tokens": 16384},
    "ctx_per_slot": 32768,
    "note": "generic defaults",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


@dataclass
class ModelCandidate:
    kind: str                     # "ollama" | "gguf"
    name: str                     # Ollama tag, or a readable name for a GGUF
    path: Optional[Path] = None   # first shard, for GGUF
    mmproj: Optional[Path] = None
    size_gb: float = 0.0
    haystack: str = ""            # text the query is matched against

    def describe(self) -> str:
        if self.kind == "ollama":
            return f"{self.name}  (Ollama)"
        return f"{self.name}  ({self.size_gb:.0f} GB GGUF, {self.path.parent})"


def ollama_candidates(host: str) -> List[ModelCandidate]:
    url = host.rstrip("/")
    url = url[:-3] if url.endswith("/v1") else url
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as r:
            data = json.load(r)
    except Exception:
        return []
    out = []
    for m in data.get("models", []):
        name = m.get("name", "")
        out.append(ModelCandidate(kind="ollama", name=name,
                                  size_gb=(m.get("size") or 0) / 1e9,
                                  haystack=_norm(name)))
    return out


def gguf_candidates(dirs: List[str]) -> List[ModelCandidate]:
    """Every GGUF model under `dirs`, one candidate per model (not per shard)."""
    out = []
    for d in dirs:
        root = Path(os.path.expanduser(str(d)))
        if not root.is_dir():
            continue
        by_model = {}
        for gguf in root.rglob("*.gguf"):
            stem = gguf.name
            # Not models in their own right: vision projectors, and MTP/EAGLE draft heads
            # that speculative decoding loads alongside a model.
            if stem.lower().startswith(("mmproj", "mtp-", "mtp_", "eagle-", "draft-")):
                continue
            if gguf.parent.name.lower() in ("mtp", "draft", "eagle"):
                continue
            shard = re.match(r"(.*)-(\d{5})-of-(\d{5})\.gguf$", stem)
            if shard:
                key = (gguf.parent, shard.group(1))
                first = shard.group(2) == "00001"
            else:
                key = (gguf.parent, stem[:-5])
                first = True
            entry = by_model.setdefault(key, {"first": None, "size": 0})
            entry["size"] += gguf.stat().st_size
            if first:
                entry["first"] = gguf
        for (parent, base), entry in by_model.items():
            if entry["first"] is None:
                continue
            # vision projector, if the model ships one next to it
            mmproj = next((p for p in list(parent.glob("mmproj*.gguf"))
                           + list(parent.parent.glob("mmproj*.gguf"))), None)
            # the containing folder usually names the model better than the file does
            label = base if len(base) >= len(parent.name) else parent.name
            out.append(ModelCandidate(
                kind="gguf", name=label, path=entry["first"], mmproj=mmproj,
                size_gb=entry["size"] / 1e9,
                haystack=_norm(f"{parent.parent.name} {parent.name} {base}")))
    return out


def resolve_model(query: str, ollama_host: str, model_dirs: List[str]) -> ModelCandidate:
    """Find the one model matching `query`, or raise with the alternatives listed.

    Matching ignores case, punctuation and quantization names, so "qwen",
    "qwen3.8-next" and "Qwen3.8-Flash-Next" all find the same model. An exact name
    always wins; otherwise every word of the query must appear.
    """
    candidates = ollama_candidates(ollama_host) + gguf_candidates(model_dirs)
    if not candidates:
        raise RuntimeError(
            f"No models found. Pull one with Ollama, or put GGUF files in one of: "
            f"{', '.join(str(d) for d in model_dirs)} (system.model_dirs).")

    q = _norm(query)
    exact = [c for c in candidates if _norm(c.name) == q or _norm(c.name.split(":")[0]) == q]
    if len(exact) == 1:
        return exact[0]

    words = [w for w in re.split(r"[^a-z0-9]+", str(query).lower()) if w]
    matches = [c for c in candidates if all(_norm(w) in c.haystack for w in words)] if words else []
    if len(matches) == 1:
        return matches[0]

    if not matches:
        # Nothing matched with the numbers included. Version numbers are the part people
        # misremember ("132B-Flash-Next"), so try again on the words alone; the result is
        # still only accepted when it is unambiguous.
        alpha = [w for w in words if w.isalpha()]
        if alpha and alpha != words:
            loose = [c for c in candidates if all(w in c.haystack for w in alpha)]
            if len(loose) == 1:
                return loose[0]
            matches = loose

    if not matches:
        listing = "\n".join(f"     - {c.describe()}" for c in candidates)
        raise RuntimeError(f"No model matches {query!r}. Available:\n{listing}")

    # Several matches: prefer one whose name starts with the query, else ask.
    starts = [c for c in matches if c.haystack.startswith(q)]
    if len(starts) == 1:
        return starts[0]
    listing = "\n".join(f"     - {c.describe()}" for c in matches)
    raise RuntimeError(f"{query!r} matches {len(matches)} models; be more specific:\n{listing}")


def read_gguf_architecture(path: Path) -> str:
    """general.architecture from a GGUF header, or "" if it cannot be read."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return ""
            _ver, _n_tensors, n_kv = struct.unpack("<IQQ", f.read(20))

            def rstr():
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", "replace")

            def rval(t):
                simple = {0: "<b", 1: "<B", 2: "<h", 3: "<H", 4: "<i", 5: "<I",
                          6: "<f", 7: "<?", 10: "<q", 11: "<Q", 12: "<d"}
                if t == 8:
                    return rstr()
                if t == 9:
                    (et,) = struct.unpack("<I", f.read(4))
                    (n,) = struct.unpack("<Q", f.read(8))
                    return [rval(et) for _ in range(n)]
                fmt = simple[t]
                return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]

            for _ in range(min(n_kv, 2000)):
                key = rstr()
                (t,) = struct.unpack("<I", f.read(4))
                val = rval(t)
                if key == "general.architecture":
                    return str(val)
    except Exception:
        return ""
    return ""


def profile_for(arch: str) -> dict:
    for known, prof in ARCH_PROFILES.items():
        if arch and known in arch:
            return prof
    return DEFAULT_PROFILE


def find_llama_server(configured: Optional[str] = None) -> str:
    for cand in ([configured] if configured else []) + LLAMA_SERVER_CANDIDATES:
        if not cand:
            continue
        p = os.path.expanduser(cand)
        if os.path.sep in p:
            if Path(p).is_file() and os.access(p, os.X_OK):
                return p
        else:
            found = shutil.which(p)
            if found:
                return found
    raise RuntimeError(
        "llama-server not found. Build llama.cpp, or set system.llama_server to its path.\n"
        "   Looked in: " + ", ".join(LLAMA_SERVER_CANDIDATES))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServer:
    """Runs llama-server for the duration of a grading run.

    Started with one slot per grading worker, a context window sized per slot, and any
    architecture-specific flags. Always stopped again, including on Ctrl-C, so a run never
    leaves a model holding memory.
    """

    def __init__(self, binary: str, model_path: Path, alias: str, parallel: int,
                 ctx_per_slot: int, mmproj: Optional[Path] = None,
                 extra_args: Optional[List[str]] = None, log_path: Optional[Path] = None):
        self.binary = binary
        self.model_path = Path(model_path)
        self.alias = alias
        self.parallel = max(1, parallel)
        self.ctx_per_slot = ctx_per_slot
        self.mmproj = mmproj
        self.extra_args = list(extra_args or [])
        self.log_path = log_path
        self.port = _free_port()
        self.proc = None
        self._log_file = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def command(self) -> List[str]:
        cmd = [self.binary,
               "--model", str(self.model_path),
               "--alias", self.alias,
               "--host", "127.0.0.1", "--port", str(self.port),
               "--ctx-size", str(self.parallel * self.ctx_per_slot),
               "--parallel", str(self.parallel),
               "--n-gpu-layers", "999",
               "--jinja",
               "--reasoning-format", "deepseek",
               "--flash-attn", "auto"]
        if self.mmproj:
            cmd += ["--mmproj", str(self.mmproj)]
        return cmd + self.extra_args

    def start(self, timeout: float = 900.0):
        self._log_file = open(self.log_path, "w") if self.log_path else subprocess.DEVNULL
        print(f"   🚀 Starting llama-server on port {self.port} "
              f"({self.parallel} slot{'s' if self.parallel != 1 else ''} x {self.ctx_per_slot:,} tokens)")
        if self.log_path:
            print(f"      server log: {self.log_path}")
        self.proc = subprocess.Popen(self.command(), stdout=self._log_file,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited with code {self.proc.returncode} while loading."
                    + (f" See {self.log_path}" if self.log_path else ""))
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        print(f"   ✅ Model loaded in {time.time() - t0:.0f}s")
                        return
            except Exception:
                pass
            time.sleep(1.0)
        self.stop()
        raise RuntimeError(f"llama-server did not become ready within {timeout:.0f}s"
                           + (f". See {self.log_path}" if self.log_path else ""))

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            print("   🛑 Stopping llama-server")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.proc = None
        if self._log_file not in (None, subprocess.DEVNULL):
            self._log_file.close()
        self._log_file = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def prepare_grader(config: dict, concurrency: int, log_dir: Optional[Path] = None):
    """Resolve system.grader.model and, for a GGUF, start a server for it.

    Rewrites the grader section of `config` in place so the rest of LaTA sees an ordinary
    backend/base_url/model_name setup. Returns the LlamaServer to stop when the run ends,
    or None when no server was started (Ollama, or an explicitly configured endpoint).
    """
    system = config.setdefault('system', {})
    grader = system.setdefault('grader', {})
    query = grader.get('model')
    if not query:
        return None                      # explicit backend/base_url/model_name: unchanged

    if grader.get('base_url') and grader.get('model_name'):
        return None                      # explicit settings win

    ollama_host = system.get('ollama_host', 'http://localhost:11434/v1')
    model_dirs = system.get('model_dirs') or DEFAULT_MODEL_DIRS

    print(f"🔎 Resolving grading model {query!r}...")
    found = resolve_model(query, ollama_host, model_dirs)
    print(f"   → {found.describe()}")

    if found.kind == "ollama":
        grader.setdefault('backend', 'ollama')
        grader['model_name'] = found.name
        grader.setdefault('ollama_options', {})
        print(f"   Served by Ollama. It must allow {concurrency} parallel request(s): "
              f"set OLLAMA_NUM_PARALLEL >= {concurrency} and restart Ollama.")
        return None

    arch = read_gguf_architecture(found.path)
    profile = profile_for(arch)
    print(f"   architecture: {arch or 'unknown'} -- {profile['note']}")

    server = LlamaServer(
        binary=find_llama_server(system.get('llama_server')),
        model_path=found.path,
        alias=_norm(found.name)[:40] or "lata-grader",
        parallel=concurrency,
        ctx_per_slot=int(grader.get('ctx_per_slot') or profile['ctx_per_slot']),
        mmproj=found.mmproj,
        extra_args=list(profile.get('server_args', [])) + list(grader.get('server_args', [])),
        log_path=(Path(log_dir) / "llama_server.log") if log_dir else None,
    )
    server.start()

    grader['backend'] = 'openai'
    grader['base_url'] = server.base_url
    grader['model_name'] = server.alias
    grader.setdefault('is_reasoning_model', True)
    merged = dict(profile.get('openai_options', {}))
    merged.update(grader.get('openai_options') or {})
    grader['openai_options'] = merged
    if profile.get('chat_template_kwargs') and not grader.get('chat_template_kwargs'):
        grader['chat_template_kwargs'] = profile['chat_template_kwargs']
    return server
