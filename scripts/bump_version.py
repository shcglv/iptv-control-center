"""Bump the version — and only the places that state the *current* version.

The previous routine was a blanket text replace across the docs. On 2026-11-26 it relabelled a
historical re-acceptance section from 1.7.0 to 1.7.7 and rewrote the old test counts, so the report
claimed a measurement from a build that never produced it. This script touches exactly six places,
matched by their surrounding words, and never rewrites another version number or a measured count.

    .\\.venv\\Scripts\\python.exe scripts\\bump_version.py 1.8.0 --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\bump_version.py 1.8.0
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Edit:
    path: str
    pattern: str
    replacement: str
    description: str


def edits(version: str) -> list[Edit]:
    old = r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
    again = r"(?P=version)"
    return [
        Edit(
            "pyproject.toml",
            rf'(?m)^version = "{old}"$',
            f'version = "{version}"',
            "project version",
        ),
        Edit(
            "src/iptvcc/__init__.py",
            rf'(?m)^__version__ = "{old}"$',
            f'__version__ = "{version}"',
            "package version",
        ),
        Edit(
            "README.md",
            rf"Status: `{old}`",
            f"Status: `{version}`",
            "README status line",
        ),
        Edit("RELEASE.md", rf"(?m)^# Release {old}$", f"# Release {version}", "release title"),
        Edit(
            "RELEASE.md",
            rf"- Version: `{old}`",
            f"- Version: `{version}`",
            "release version bullet",
        ),
        Edit(
            "START-HERE.md",
            rf"\*\*IPTV Control Center {old}\*\*",
            f"**IPTV Control Center {version}**",
            "front-door title",
        ),
        Edit(
            "docs/STATUS.md",
            rf"\*\*Version {old} · ([^*]+) · tagged `v{again}`\*\*",
            rf"**Version {version} · \1 · tagged `v{version}`**",
            "STATUS header",
        ),
        Edit(
            "docs/VERIFICATION.md",
            rf"\*\*Version:\*\* {old} \(tagged `v{again}`\)",
            f"**Version:** {version} (tagged `v{version}`)",
            "verification header",
        ),
    ]


def current_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([^"]+)"$', text)
    if match is None:
        raise SystemExit("pyproject.toml has no version line")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="new version, for example 1.8.0")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        print(f"not a semantic version: {args.version}")
        return 2
    before = current_version()
    if before == args.version:
        print(f"already at {before}; nothing to do")
        return 0

    print(f"bumping {before} -> {args.version}")
    pending: list[tuple[Edit, str, str]] = []
    for edit in edits(args.version):
        path = ROOT / edit.path
        text = path.read_text(encoding="utf-8")
        match = re.search(edit.pattern, text)
        if match is None:
            print(f"  MISSING  {edit.path}: no match for {edit.description}")
            return 1
        if match.group("version") not in {"", before} or (
            "version" in match.groupdict() and match.group("version") != before
        ):
            print(
                f"  ODD      {edit.path}: {edit.description} says "
                f"{match.group('version')}, expected {before}"
            )
            return 1
        updated = re.sub(edit.pattern, edit.replacement, text, count=1)
        pending.append((edit, text, updated))
        print(f"  ok       {edit.path}: {edit.description}")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    for edit, _text, updated in pending:
        (ROOT / edit.path).write_text(updated, encoding="utf-8", newline="\n")
    print(f"done: {len(pending)} place(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
