"""Device tokens and public channel keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

TOKEN_PREFIX = "ipt_"
_PUBLIC_ALPHABET = string.ascii_lowercase + string.digits


def generate_device_token() -> str:
    """Return a fresh opaque device token (shown to the user exactly once)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """Short non-secret prefix used for lookups and UI display."""
    return token[len(TOKEN_PREFIX) :][:8]


def new_public_key() -> str:
    """Short stable key used in playlist URLs and as Jellyfin's ``channel-id``."""
    return "".join(secrets.choice(_PUBLIC_ALPHABET) for _ in range(12))


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
