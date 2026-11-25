"""In-process sliding-window rate limiting for the public delivery endpoints.

Deliberately simple: one process serves both the API and the UI, so a dict of deques is enough and
adds no Redis dependency. The limit is per token, because the token is what a player holds; the
client address is only a secondary key for anonymous probing.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from .config import get_settings


@dataclass
class SlidingWindow:
    limit: int
    window_seconds: float
    _events: dict[str, deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def hit(self, key: str, *, now: float | None = None) -> tuple[bool, int, float]:
        """Record a request. Returns ``(allowed, remaining, retry_after_seconds)``."""
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window_seconds
        with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(0.0, bucket[0] + self.window_seconds - moment)
                return False, 0, retry_after
            bucket.append(moment)
            return True, max(0, self.limit - len(bucket)), 0.0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


_limiter: SlidingWindow | None = None


def limiter() -> SlidingWindow:
    global _limiter
    settings = get_settings()
    limit = settings.public_rate_limit_per_minute
    if _limiter is None or _limiter.limit != limit:
        _limiter = SlidingWindow(limit=limit, window_seconds=60.0)
    return _limiter


def reset_limiter() -> None:
    """Test hook: drop all recorded hits."""
    global _limiter
    _limiter = None


def _client_key(request: Request, token: str) -> str:
    address = request.client.host if request.client else "unknown"
    return f"{token[:16]}:{address}"


async def public_rate_limit(request: Request) -> None:
    """FastAPI dependency for ``/p/`` routes: 429 with ``Retry-After`` when the window is full."""
    settings = get_settings()
    if settings.public_rate_limit_per_minute <= 0:
        return
    token = request.path_params.get("token", "")
    allowed, _remaining, retry_after = limiter().hit(_client_key(request, str(token)))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="too many requests for this device token",
            headers={
                "Retry-After": str(int(retry_after) + 1),
                "X-RateLimit-Limit": str(settings.public_rate_limit_per_minute),
                "X-RateLimit-Remaining": "0",
            },
        )
