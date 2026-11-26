"""Documentation consistency check.

Version numbers and migration heads in prose go stale silently. This finds the drift that manual
review kept missing:

- version agreement across pyproject, the package, README, RELEASE, STATUS and VERIFICATION
- the migration head quoted in DEPLOYMENT.md versus the actual Alembic head
- relative Markdown links in docs/ and the root that point at files which do not exist

    .\\.venv\\Scripts\\python.exe scripts\\check_docs.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
NOTES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def package_version() -> str:
    pyproject = read(ROOT / "pyproject.toml")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if match is None:
        raise SystemExit("pyproject.toml has no version")
    return match.group(1)


def module_version() -> str:
    init = read(ROOT / "src" / "iptvcc" / "__init__.py")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if match is None:
        raise SystemExit("__init__.py has no __version__")
    return match.group(1)


def alembic_head() -> str:
    sys.path.insert(0, str(ROOT))
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    if head is None:
        raise SystemExit("no alembic head")
    return head


def check_versions(version: str) -> None:
    if module_version() != version:
        fail(f"src/iptvcc/__init__.py has {module_version()}, pyproject has {version}")

    expectations = {
        "README.md": [rf"Status: `{re.escape(version)}`"],
        "RELEASE.md": [rf"{re.escape(version)}", r"1\.\d+\.\d+"],
        "docs/STATUS.md": [rf"Version {re.escape(version)}"],
        "docs/VERIFICATION.md": [rf"\*\*Version:\*\* {re.escape(version)}"],
    }
    for relative, patterns in expectations.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"{relative} is missing")
            continue
        text = read(path)
        for pattern in patterns:
            if not re.search(pattern, text):
                fail(f"{relative} does not mention {version} (pattern: {pattern})")

    # Any other version in RELEASE.md or README.md that looks like our own is drift.
    for relative in ("README.md", "RELEASE.md"):
        text = read(ROOT / relative)
        for found in set(re.findall(r"\b1\.\d+\.\d+\b", text)):
            if found != version:
                fail(f"{relative} still mentions version {found}")


def check_migration_head(head: str) -> None:
    for relative in ("docs/DEPLOYMENT.md", "README.md", "docs/ARCHITECTURE.md", "docs/RUNBOOK.md"):
        path = ROOT / relative
        if not path.is_file():
            continue
        for found in set(re.findall(r"\b[0-9a-f]{12}\b", read(path))):
            if found != head and re.fullmatch(r"[0-9a-f]{12}", found):
                NOTES.append(f"{relative} mentions revision {found}; head is {head}")


def check_test_counts() -> None:
    """The counts in VERIFICATION.md must match what pytest actually collects.

    They drifted twice: the report described 232 and later 266 tests when the suite had grown. The
    count is cheap to obtain (`--collect-only` takes a few seconds) and comparing it turns a hand-
    maintained number into a checked one. "passed + skipped" is compared, because which tests pass
    depends on the environment; how many exist does not.
    """
    verification = read(ROOT / "docs" / "VERIFICATION.md")
    # The count appears both in a plain code block and in bold table cells; allow the emphasis.
    documented = {
        (int(passed), int(skipped))
        for passed, skipped in re.findall(
            r"(\d+)\s+passed\**\s*,?\s*\**\s*(\d+)\s+skipped", verification
        )
    }
    if not documented:
        fail("docs/VERIFICATION.md has no 'N passed, M skipped' line to verify")
        return

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        fail("pytest --collect-only failed; cannot verify the documented test count")
        return

    collected = None
    for line in reversed(completed.stdout.splitlines()):
        match = re.search(r"(\d+) tests? collected", line)
        if match is not None:
            collected = int(match.group(1))
            break
    if collected is None:
        fail("could not read the collected test count from pytest")
        return

    for passed, skipped in sorted(documented):
        if passed + skipped != collected:
            fail(
                f"docs/VERIFICATION.md documents {passed + skipped} tests "
                f"({passed} passed + {skipped} skipped); pytest collects {collected}"
            )
    if len(documented) > 1:
        NOTES.append(f"VERIFICATION.md states {len(documented)} different test counts")


def check_links() -> None:
    targets = [ROOT / "README.md", ROOT / "RELEASE.md", ROOT / "CHANGELOG.md"]
    targets += sorted((ROOT / "docs").rglob("*.md"))
    pattern = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
    for path in targets:
        if not path.is_file():
            continue
        for link in pattern.findall(read(path)):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / link).resolve()
            if not resolved.exists():
                fail(f"{path.relative_to(ROOT)} links to missing {link}")


def main() -> int:
    version = package_version()
    print(f"version: {version}")
    check_versions(version)

    head = alembic_head()
    print(f"alembic head: {head}")
    check_migration_head(head)

    check_links()
    check_test_counts()

    for note in NOTES:
        print(f"note: {note}")
    if FAILURES:
        print(f"\n{len(FAILURES)} problem(s):")
        for problem in FAILURES:
            print(f"  - {problem}")
        return 1
    print("\nRESULT: DOCUMENTATION CONSISTENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
