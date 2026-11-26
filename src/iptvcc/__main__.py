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


def windows_selector_loop() -> asyncio.AbstractEventLoop:
    """A selector loop for uvicorn on Windows.

    uvicorn 0.36+ stopped consulting the event-loop policy: it passes an explicit loop factory, and
    on Windows that factory builds a ``ProactorEventLoop``. Setting the policy alone is therefore
    not enough — the factory has to be replaced, which is what the ``loop`` option below does.
    """
    return asyncio.SelectorEventLoop()


def _loop_option() -> str:
    settings = get_settings()
    if sys.platform == "win32" and settings.database_url.startswith("postgres"):
        return "iptvcc.__main__:windows_selector_loop"
    return "auto"


def main() -> None:
    _configure_event_loop_for_postgres()
    settings = get_settings()
    uvicorn.run(
        "iptvcc.api:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
        proxy_headers=True,
        loop=_loop_option(),
    )


if __name__ == "__main__":
    main()
