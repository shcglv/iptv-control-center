"""The version tool must keep touching exactly the places that state the current version.

It replaced a blanket text replace that had relabelled historical measurements; these tests make sure
it stays narrow and that its dry run is genuinely dry.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bump_version.py"

TRACKED = [
    "pyproject.toml",
    "src/iptvcc/__init__.py",
    "README.md",
    "RELEASE.md",
    "START-HERE.md",
    "docs/STATUS.md",
    "docs/VERIFICATION.md",
]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _current() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([^"]+)"$', text)
    assert match is not None
    return match.group(1)


def _snapshot() -> dict[str, bytes]:
    return {name: (ROOT / name).read_bytes() for name in TRACKED}


def test_the_current_version_is_a_no_op() -> None:
    completed = _run(_current(), "--dry-run")
    assert completed.returncode == 0
    assert "already at" in completed.stdout


def test_a_dry_run_matches_every_site_and_writes_nothing() -> None:
    before = _snapshot()
    completed = _run("9.9.9", "--dry-run")
    after = _snapshot()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("  ok       ") == 8, completed.stdout
    assert "dry run: nothing written" in completed.stdout
    assert before == after, "the dry run modified files"


def test_a_trailing_or_malformed_version_is_refused() -> None:
    completed = _run("1.8", "--dry-run")
    assert completed.returncode == 2
    assert "not a semantic version" in completed.stdout


@pytest.mark.parametrize("path", TRACKED)
def test_every_target_file_exists(path: str) -> None:
    assert (ROOT / path).is_file(), path


def test_a_real_run_updates_every_site_and_spares_history(tmp_path: Path) -> None:
    """The bug this guards: two edits to one file, where the second discarded the first."""
    for name in TRACKED:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())

    history = tmp_path / "docs" / "VERIFICATION.md"
    original = history.read_text(encoding="utf-8")
    marker = "| Backend, SQLite | **272 passed**, 16 skipped, 221 s |"
    history.write_text(original + "\n" + marker + "\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "9.9.9", "--root", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    release = (tmp_path / "RELEASE.md").read_text(encoding="utf-8")
    assert release.startswith("# Release 9.9.9"), release.splitlines()[0]
    assert "- Version: `9.9.9`" in release

    assert "Status: `9.9.9`" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8").count('version = "9.9.9"') == 1

    after = history.read_text(encoding="utf-8")
    assert marker in after, "a historical measurement was rewritten"
