"""The repository's own documentation must agree with its code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_documentation_is_consistent() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_docs.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "DOCUMENTATION CONSISTENT" in completed.stdout
