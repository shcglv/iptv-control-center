"""Scheduler drill: the autonomous loop must actually tick in a running process.

The scheduler is unit-tested with fakes. This verifies the wiring end to end: start the real
application with a short tick, wait, and read the persisted scheduler state. No streams are probed,
so it needs neither network access nor a provider.

    .\\.venv\\Scripts\\python.exe scripts\\verify_scheduler.py
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 18097
DATABASE = ROOT / "data" / "scheduler-drill.db"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    DATABASE.unlink(missing_ok=True)
    env = {
        **os.environ,
        "IPTV_DATABASE_URL": f"sqlite+aiosqlite:///{DATABASE.as_posix()}",
        "IPTV_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "IPTV_APP_ENV": "test",
        "IPTV_HOST": "127.0.0.1",
        "IPTV_PORT": str(PORT),
        "IPTV_PUBLIC_BASE_URL": f"http://127.0.0.1:{PORT}",
        "IPTV_SCHEDULER_ENABLED": "true",
        "IPTV_SCHEDULER_TICK_SECONDS": "2",
        "IPTV_SCHEDULER_HOT_INTERVAL_SECONDS": "5",
        "IPTV_SCHEDULER_COLD_INTERVAL_SECONDS": "5",
        "IPTV_TELEGRAM_ENABLED": "false",
    }
    os.environ.update(env)  # the reader below must open the same database
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        print(migrated.stderr[-1200:])
        return 2

    log_path = ROOT / "data" / "scheduler-drill.log"
    server = subprocess.Popen(
        [sys.executable, "-m", "iptvcc"],
        cwd=ROOT,
        env=env,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/healthz", timeout=2
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.5)
        else:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-1200:])
            print("RESULT: FAILED - the application did not start")
            return 3

        # One tick is 2 s; give the loop room for at least two.
        time.sleep(8)

        async def read_state() -> tuple[int, int, str | None]:
            from sqlalchemy import text

            from iptvcc import db

            async with db.get_session_factory()() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT rounds, probed_total, last_tick_epoch "
                            "FROM scheduler_state LIMIT 1"
                        )
                    )
                ).first()
            await db.dispose_engine()
            if row is None:
                return 0, 0, None
            return int(row[0]), int(row[1]), str(row[2]) if row[2] is not None else None

        rounds, probed, last_tick = asyncio.run(read_state())
        print(f"after 8 s: rounds={rounds} probed_total={probed} last_tick={last_tick}")

        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        errors = [
            line
            for line in log_text.splitlines()
            if "scheduler" in line.lower() and "error" in line.lower()
        ]
        for line in errors[:3]:
            print("  log:", line.strip())

        if rounds < 1:
            print("RESULT: FAILED - the scheduler never recorded a tick")
            return 4
        if errors:
            print("RESULT: FAILED - the scheduler logged an error")
            return 5
        print(f"RESULT: SCHEDULER DRILL PASSED ({rounds} ticks in a running process)")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        _ = re


if __name__ == "__main__":
    raise SystemExit(main())
