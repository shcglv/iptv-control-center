"""``python -m iptvcc`` entry point."""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "iptvcc.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
        proxy_headers=True,
    )
    _ = settings


if __name__ == "__main__":
    main()
