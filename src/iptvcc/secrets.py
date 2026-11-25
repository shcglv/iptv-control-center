"""Envelope encryption for values that must not be readable at rest.

Provider stream URLs usually embed credentials (``/live/user/pass/123.ts``). They are stored
encrypted with AES-256-GCM and decrypted only at delivery and probe time.

Envelope format: ``v1:<base64(nonce || ciphertext)>``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

logger = logging.getLogger(__name__)

_PREFIX = "v1:"
_NONCE_BYTES = 12

# Development-only key. Production must set IPTV_ENCRYPTION_KEY; otherwise the process
# would happily encrypt with a public constant, which is worse than failing loudly.
_DEV_KEY_SEED = b"iptv-control-center-dev-key-do-not-use-in-production"

_warned = False


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("IPTV_ENCRYPTION_KEY must be base64-encoded") from exc
    if len(key) != 32:
        raise ValueError("IPTV_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def _key() -> bytes:
    global _warned
    settings = get_settings()
    if settings.encryption_key:
        return _decode_key(settings.encryption_key)

    if settings.is_production:
        raise RuntimeError("IPTV_ENCRYPTION_KEY is required in production")

    if not _warned:
        logger.warning(
            "IPTV_ENCRYPTION_KEY is not set: using the development key. "
            "Set a random 32-byte key before exposing this instance."
        )
        _warned = True
    return hashlib.sha256(_DEV_KEY_SEED).digest()


def is_development_key(raw: str) -> bool:
    """True when the configured key is missing or is the public development constant."""
    if not raw:
        return True
    try:
        return _decode_key(raw) == hashlib.sha256(_DEV_KEY_SEED).digest()
    except ValueError:
        return False


def encrypt(plaintext: str) -> str:
    """Encrypt ``plaintext`` into a versioned, URL-safe envelope."""
    if plaintext == "":
        return ""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt(envelope: str) -> str:
    """Decrypt an envelope produced by :func:`encrypt`."""
    if envelope == "":
        return ""
    if not envelope.startswith(_PREFIX):
        raise ValueError("unsupported secret envelope version")
    blob = base64.urlsafe_b64decode(envelope[len(_PREFIX) :].encode("ascii"))
    nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return AESGCM(_key()).decrypt(nonce, ciphertext, None).decode("utf-8")


def mask_url(url: str) -> str:
    """Return ``scheme://host[:port]/…`` with credentials and path segments removed.

    Used for logs and API responses so provider credentials never leak.
    """
    if "://" not in url:
        return "…"
    scheme, rest = url.split("://", 1)
    authority, _, _path = rest.partition("/")
    host = authority.rsplit("@", 1)[-1]
    return f"{scheme}://{host}/…"
