"""The demonstration script runs end to end, so what is shown to the teacher cannot rot."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_demonstration_runs_and_ends_with_the_same_results_in_the_service() -> None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py"), "--kind", "process"],
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    assert "an operator who publishes: 403" in out
    assert "a clerk who approves: 403" in out
    assert "the key that starts runs may not approve: 403" in out
    assert "SAME RESULTS as the core" in out
    assert "Done: the core and the service were stopped." in out
