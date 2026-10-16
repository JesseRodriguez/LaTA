"""Run every test script: `uv run python tests/run_all.py`. Uses made-up students only."""
import subprocess, sys
from pathlib import Path
here = Path(__file__).parent
failed = []
for t in sorted(here.glob("test_*.py")):
    r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True)
    last = [l for l in r.stdout.splitlines() if l.strip()][-1:] or ["(no output)"]
    print(f"{t.name:<22} {'ok ' if r.returncode == 0 else 'FAIL'}  {last[0].strip()}")
    if r.returncode:
        failed.append(t.name)
        print("\n".join("    " + l for l in (r.stdout + r.stderr).splitlines()[-15:]))
sys.exit(1 if failed else 0)
