"""``python -m iptvcc`` entry point."""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from .config import get_settings


def _configure_event_loop_for_postgres() -> None:
    """psycopg's async mode cannot use Windows' default Proactor loop.

    Production runs on Linux, where this is a no-op; it exists so a developer can point the app at
    PostgreSQL on Windows without hitting "Psycopg cannot use the 'ProactorEventLoop'".
    """
    settings = get_settings()
    if sys.platform == "win32" and settings.database_url.startswith("postgres"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    _configure_event_loop_for_postgres()
    uvicorn.run(
        "iptvcc.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
        proxy_headers=True,
    )
