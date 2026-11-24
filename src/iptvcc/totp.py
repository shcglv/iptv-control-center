"""TOTP (RFC 6238) helpers for the second factor."""

from __future__ import annotations

import pyotp

ISSUER = "IPTV Control Center"
DIGITS = 6
INTERVAL = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL).provisioning_uri(
        name=account, issuer_name=ISSUER
    )


def verify(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Accept the current code and one step either side (clock drift tolerance)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    return pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL).verify(
        code, valid_window=valid_window
    )
