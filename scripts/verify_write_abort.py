"""Drill: a failed write must abort the request, not return success.

The in-process test transport re-raises either way, so it cannot distinguish "aborted before the
response" from "raised after it". A real uvicorn server can, and this drill uses one twice:

* a pair of routes with an identical failing dependency — one with ``scope="function"``, one without
  — to show the mechanism and the status each produces;
* the real application, writing a profile while every commit fails.

    .\\.venv\\Scripts\\python.exe scripts\\verify_write_abort.py
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "data" / "write-abort.db"

os.environ["IPTV_APP_ENV"] = "test"
os.environ["IPTV_DATABASE_URL"] = f"sqlite+aiosqlite:///{DATABASE.as_posix()}"
os.environ["IPTV_ENCRYPTION_KEY"] = base64.b64encode(b"0" * 32).decode()
os.environ["IPTV_SCHEDULER_ENABLED"] = "false"
os.environ["IPTV_TELEGRAM_ENABLED"] = "false"
os.environ["IPTV_PUBLIC_BASE_URL"] = "http://127.0.0.1:18086"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402
import pyotp  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from iptvcc.api import app  # noqa: E402
from iptvcc.auth import create_admin_user  # noqa: E402
from iptvcc.db import create_schema, dispose_engine  # noqa: E402
from iptvcc.totp import new_secret  # noqa: E402

EMAIL = "write-abort@local"
PASSWORD = "correct-horse-battery"
FAILURES: list[str] = []


def serve(application: FastAPI, port: int) -> uvicorn.Server:
    """Start a server and confirm it is listening.

    A port left in TIME_WAIT makes the bind fail, and without this check the drill then reported a
    verification failure against a server that never existed. Two attempts, then a clear error.
    """
    for attempt in range(2):
        server = uvicorn.Server(
            uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(150):
            if server.started:
                return server
            time.sleep(0.1)
        time.sleep(2 + attempt * 2)
    raise SystemExit(f"could not bind 127.0.0.1:{port}; a previous server may still hold it")


def comparison_app() -> FastAPI:
    plain = FastAPI()

    async def failing_dependency():
        yield "value"
        raise RuntimeError("commit failed")

    @plain.get("/before")
    async def before(_: str = Depends(failing_dependency, scope="function")) -> dict[str, bool]:
        return {"ok": True}

    @plain.get("/after")
    async def after(_: str = Depends(failing_dependency)) -> dict[str, bool]:
        return {"ok": True}

    return plain


def attempt(client: httpx.Client, method: str, url: str, **kwargs: object) -> int | None:
    """Return the status, or None when the connection was aborted (also "no success")."""
    try:
        return client.request(method, url, **kwargs).status_code
    except httpx.HTTPError:
        return None


def check(label: str, status: int | None, *, expect_error: bool) -> None:
    ok = (status is None or status >= 500) if expect_error else status == 200
    shown = "connection aborted" if status is None else f"HTTP {status}"
    print(f"  [{'ok' if ok else 'fail'}] {label:<44} {shown}")
    if not ok:
        FAILURES.append(f"{label}: got {shown}")


async def seed() -> str:
    await create_schema()
    from iptvcc import db

    secret = new_secret()
    async with db.get_session_factory()() as session:
        await create_admin_user(session, email=EMAIL, password=PASSWORD, totp_secret=secret)
        await session.commit()
    return secret


async def main() -> int:
    DATABASE.unlink(missing_ok=True)
    secret = await seed()

    plain = comparison_app()
    server = serve(plain, 18087)
    try:
        client = httpx.Client(base_url="http://127.0.0.1:18087", trust_env=False, timeout=10)
        # A dependency that raises after the response can reset the connection on Windows instead of
        # returning 500; attempt() reports that as "no success", which is what the check asserts.
        check(
            "dependency ends after the response",
            attempt(client, "GET", "/after"),
            expect_error=False,
        )
        check(
            "dependency ends before the response",
            attempt(client, "GET", "/before"),
            expect_error=True,
        )
    finally:
        server.should_exit = True
        time.sleep(0.5)

    server = serve(app, 18086)
    try:
        client = httpx.Client(base_url="http://127.0.0.1:18086", trust_env=False, timeout=15)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": PASSWORD, "totp": pyotp.TOTP(secret).now()},
        )
        if login.status_code != 200:
            print(f"RESULT: FAILED - could not log in ({login.status_code})")
            return 2

        original_commit = AsyncSession.commit

        async def boom(self: AsyncSession) -> None:
            raise RuntimeError("disk gone")

        AsyncSession.commit = boom  # type: ignore[method-assign]
        try:
            status = attempt(
                client,
                "POST",
                "/api/v1/profiles",
                json={"name": "Write abort"},
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
            )
            check("profile write with a failing commit", status, expect_error=True)
        finally:
            AsyncSession.commit = original_commit  # type: ignore[method-assign]
    finally:
        server.should_exit = True
        time.sleep(0.5)
        await dispose_engine()

    if FAILURES:
        print(f"RESULT: WRITE-ABORT DRILL FAILED ({len(FAILURES)} check(s))")
        return 1
    print("RESULT: WRITE-ABORT DRILL PASSED (a failed write is a server error, not a success)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
