"""Run the whole verification suite against the current checkout.

Each drill is a separate script because each needs something different — Docker, the internet, a
PostgreSQL container, or nothing at all. This runs them in order, skips what the machine cannot
support, and prints one summary.

    .\\.venv\\Scripts\\python.exe scripts\\verify_all.py
    .\\.venv\\Scripts\\python.exe scripts\\verify_all.py --skip-docker --skip-network
    .\\.venv\\Scripts\\python.exe scripts\\verify_all.py --only check_docs --only scheduler
    .\\.venv\\Scripts\\python.exe scripts\\verify_all.py --base https://iptv.joxit.ru
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class Drill:
    name: str
    script: str
    needs: frozenset[str] = frozenset()
    extra_args: tuple[str, ...] = ()


DRILLS: list[Drill] = [
    Drill("docs", "scripts/check_docs.py"),
    Drill("spec", "scripts/check_spec.py"),
    Drill("scheduler", "scripts/verify_scheduler.py"),
    Drill("telegram", "scripts/verify_telegram.py"),
    Drill("write-abort", "scripts/verify_write_abort.py"),
    Drill("panel", "scripts/verify_panel.py", needs=frozenset({"docker"})),
    Drill("decoder", "scripts/verify_decoder.py", needs=frozenset({"docker", "network"})),
    Drill("upgrade", "scripts/verify_upgrade.py", needs=frozenset({"docker"})),
    Drill("restore", "scripts/verify_restore.py", needs=frozenset({"docker"})),
    Drill("postgres-container", "scripts/verify_pg_container.py", needs=frozenset({"docker"})),
    Drill(
        "smoke",
        "scripts/smoke_public.py",
        needs=frozenset({"network"}),
        extra_args=("--base", "REPLACE"),
    ),
]


def docker_available() -> bool:
    completed = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True
    )
    return completed.returncode == 0


def network_available() -> bool:
    import socket

    try:
        with socket.create_connection(("github.com", 443), timeout=5):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="run only these drills")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--base", default="", help="public base URL for the smoke drill")
    parser.add_argument("--timeout", type=float, default=1800.0)
    args = parser.parse_args()

    available = set()
    if not args.skip_docker and docker_available():
        available.add("docker")
    if not args.skip_network and network_available():
        available.add("network")

    selected = [drill for drill in DRILLS if not args.only or drill.name in args.only]
    if not selected:
        print(f"no drills match {args.only}; known: {', '.join(d.name for d in DRILLS)}")
        return 2

    print(f"capabilities: {', '.join(sorted(available)) or 'none'}")
    if args.only and "smoke" in args.only and not args.base:
        print("the smoke drill needs --base")
        return 2

    results: list[tuple[str, str, float]] = []
    for drill in selected:
        missing = drill.needs - available
        if missing:
            print(f"\n=== {drill.name}: skipped (needs {', '.join(sorted(missing))})")
            results.append((drill.name, "skipped", 0.0))
            continue

        command = [sys.executable, str(ROOT / drill.script)]
        for extra in drill.extra_args:
            command.append(args.base if extra == "REPLACE" else extra)

        print(f"\n=== {drill.name}: running", flush=True)
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
        elapsed = time.perf_counter() - started
        status = "passed" if completed.returncode == 0 else "FAILED"
        results.append((drill.name, status, elapsed))

        combined = (completed.stdout + completed.stderr).splitlines()
        tail = [
            line
            for line in combined
            if line.startswith("RESULT:") or "FAILED" in line or "Traceback" in line
        ]
        for line in tail[-4:]:
            print(f"   {line.strip()}")
        if status != "passed":
            # A failing drill must explain itself: the matching lines above say nothing about a
            # crash, which is exactly when a tail is needed.
            print("   --- last lines of output ---")
            for line in combined[-25:]:
                print(f"   {line[:200]}")
        print(f"   {status} in {elapsed:.1f}s")

    print("\n| drill | result | time |")
    print("|---|---|---|")
    for name, status, elapsed in results:
        print(f"| {name} | {status} | {elapsed:.1f}s |")

    failed = [name for name, status, _ in results if status == "FAILED"]
    skipped = [name for name, status, _ in results if status == "skipped"]
    if failed:
        print(f"\nVERIFICATION FAILED: {', '.join(failed)}")
        return 1
    ran = len(results) - len(skipped)
    if ran == 0:
        # "PASSED (0 drills, N skipped)" was a green verdict for verifying nothing: the machine
        # lacked Docker and the network, so every drill was skipped and the summary said PASSED.
        print(f"\nVERIFICATION DID NOT RUN ({len(skipped)} drill(s) skipped, none executed)")
        return 1
    print(f"\nVERIFICATION PASSED ({ran} drills, {len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
