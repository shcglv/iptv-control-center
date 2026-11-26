"""The verification runner must not report success for work it did not do."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_all.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def test_an_unknown_drill_is_refused() -> None:
    completed = _run("--only", "no-such-drill")
    assert completed.returncode == 2
    assert "no drills match" in completed.stdout


def test_all_skipped_is_not_success() -> None:
    """Without Docker the panel drill cannot run; a summary that says PASSED would be a lie."""
    completed = _run("--only", "panel", "--skip-docker", "--skip-network")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "DID NOT RUN" in completed.stdout
    assert "PASSED" not in completed.stdout


def test_a_drill_that_runs_reports_success() -> None:
    completed = _run("--only", "spec", "--skip-docker", "--skip-network")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "VERIFICATION PASSED (1 drills, 0 skipped)" in completed.stdout
