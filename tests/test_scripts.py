"""Every operator-facing command must at least start.

`python -m iptvcc` shipped for several releases doing nothing at all: the module defined `main()`
without a `__main__` guard, so it imported and exited silently. No test touched it, because tests
import the application directly. These checks make that class of defect fail the suite.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HELP_COMMANDS = [
    [sys.executable, "-m", "iptvcc.cli", "--help"],
    [sys.executable, str(ROOT / "scripts" / "backup.py"), "--help"],
    [sys.executable, str(ROOT / "scripts" / "catalog_import.py"), "--help"],
    [sys.executable, str(ROOT / "scripts" / "bench_scale.py"), "--help"],
]


@pytest.mark.parametrize("command", HELP_COMMANDS, ids=lambda cmd: Path(cmd[2]).name)
def test_command_prints_help_and_exits_zero(command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[-800:]
    assert "usage:" in completed.stdout.lower()


def test_launcher_has_a_main_guard() -> None:
    """`python -m iptvcc` must actually run `main()`, not merely define it."""
    source = (ROOT / "src" / "iptvcc" / "__main__.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "main()" in source
