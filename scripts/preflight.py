"""Run the pre-deployment checks and print a verdict.

    .\\.venv\\Scripts\\python.exe scripts\\preflight.py
    docker exec <container> python scripts/preflight.py

Exit code 0 when nothing failed, 1 when at least one check fails. Warnings do not fail the run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from iptvcc.config import get_settings
from iptvcc.preflight import FAIL, WARN, has_failures, run_preflight


async def main() -> int:
    settings = get_settings()
    results = await run_preflight(settings, alembic_ini=Path("alembic.ini"))

    width = max(len(result.name) for result in results)
    for result in results:
        marker = {WARN: "!", "ok": "+"}.get(result.status, "x")
        print(f"[{marker}] {result.name:<{width}}  {result.detail}")

    failures = sum(1 for result in results if result.status == FAIL)
    warnings = sum(1 for result in results if result.status == WARN)
    print(f"\n{failures} failed, {warnings} warnings, {len(results)} checks")
    return 1 if has_failures(results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) if sys.version_info >= (3, 7) else 1)
