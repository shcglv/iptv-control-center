"""Logging setup and request correlation.

One line per request with a correlation id, the route *template* (never the raw path: it contains
device tokens), status and duration. Response bodies and query strings are never logged.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure one stdout handler with request ids. Safe to call more than once."""
    root = logging.getLogger()
    if any(getattr(handler, "_iptv_configured", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(RequestIdFilter())
    handler._iptv_configured = True  # type: ignore[attr-defined]
    root.handlers = [handler]
    root.setLevel(level.upper())


def _route_template(request: Request) -> str:
    """The matched route pattern, so path parameters (tokens, keys) never reach the log."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    # 404s have no route: fall back to the first segment, which cannot contain a token.
    first = request.url.path.strip("/").split("/", 1)[0]
    return f"/{first}/…" if first else "/"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        logger = logging.getLogger("iptvcc.request")
        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - started) * 1000
            logger.exception(
                "request failed method=%s route=%s duration_ms=%.1f",
                request.method,
                _route_template(request),
                duration,
            )
            raise
        else:
            duration = (time.perf_counter() - started) * 1000
            logger.info(
                "method=%s route=%s status=%s duration_ms=%.1f",
                request.method,
                _route_template(request),
                response.status_code,
                duration,
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)
