"""Admin authentication: password + TOTP, server-side sessions, CSRF.

Sessions are opaque random tokens; only their SHA-256 is stored. The CSRF token is returned in
the login response and must be sent as ``X-CSRF-Token`` on every unsafe request.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import passwords, totp
from .config import get_settings
from .db import get_session
from .models import AdminSession, AdminUser
from .secrets import decrypt, encrypt

SESSION_COOKIE = "iptv_session"
SESSION_TTL = timedelta(hours=12)
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Very small in-process guard; a proper limiter arrives with the hardening phase.
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 5
_login_attempts: dict[str, list[float]] = {}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes even for ``timezone=True`` columns."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    attempts = [
        stamp for stamp in _login_attempts.get(key, []) if now - stamp < _LOGIN_WINDOW_SECONDS
    ]
    _login_attempts[key] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS


def _record_attempt(key: str) -> None:
    _login_attempts.setdefault(key, []).append(time.monotonic())


def reset_login_rate_limit() -> None:
    """Clear the in-process login counter (used by tests and after a config change)."""
    _login_attempts.clear()


async def create_admin_user(
    session: AsyncSession, *, email: str, password: str, totp_secret: str | None = None
) -> tuple[AdminUser, str]:
    """Create an admin. Returns the user and the plaintext TOTP secret (shown once)."""
    email = email.strip().lower()
    existing = await session.scalar(select(AdminUser).where(AdminUser.email == email))
    if existing is not None:
        raise ValueError("an admin with this email already exists")

    secret = totp_secret or totp.new_secret()
    user = AdminUser(
        email=email,
        password_hash=passwords.hash_password(password),
        totp_secret_enc=encrypt(secret),
    )
    session.add(user)
    await session.flush()
    return user, secret


async def reset_admin_password(session: AsyncSession, *, email: str, password: str) -> bool:
    """Replace an admin password and revoke every session it holds.

    The panel has no password form, and a deployment whose admin password was set once by an
    operator had no way to rotate it. Returns False when no admin has that email.
    """
    user = await session.scalar(select(AdminUser).where(AdminUser.email == email.strip().lower()))
    if user is None:
        return False
    user.password_hash = passwords.hash_password(password)
    await session.execute(delete(AdminSession).where(AdminSession.user_id == user.id))
    return True


async def authenticate(
    session: AsyncSession, *, email: str, password: str, code: str, client_key: str
) -> AdminUser | None:
    if _rate_limited(client_key):
        raise HTTPException(status_code=429, detail="too many login attempts, try again later")

    user = await session.scalar(
        select(AdminUser).where(
            AdminUser.email == email.strip().lower(), AdminUser.is_active.is_(True)
        )
    )
    _record_attempt(client_key)
    # Always run a hash verification to keep timing uniform for unknown users.
    stored_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = passwords.verify_password(password, stored_hash)
    if user is None or not password_ok:
        return None

    secret = decrypt(user.totp_secret_enc)
    if not totp.verify(secret, code):
        return None

    user.last_login_at = datetime.now(UTC)
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    return user


async def open_session(
    session: AsyncSession, *, user: AdminUser, ip: str | None, user_agent: str | None
) -> tuple[str, str]:
    """Create a session. Returns ``(session_token, csrf_token)`` — both shown once."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    row = AdminSession(
        user_id=user.id,
        token_hash=_hash(token),
        csrf_hash=_hash(csrf),
        expires_at=datetime.now(UTC) + SESSION_TTL,
        ip=ip,
        user_agent=(user_agent or "")[:300],
    )
    session.add(row)
    await session.flush()
    return token, csrf


async def revoke_session(session: AsyncSession, token: str) -> None:
    row = await session.scalar(select(AdminSession).where(AdminSession.token_hash == _hash(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


def set_session_cookie(response: object, token: str) -> None:
    settings = get_settings()
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(  # type: ignore[attr-defined]
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: object) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")  # type: ignore[attr-defined]


async def get_current_admin(
    request: Request, session: AsyncSession = Depends(get_session)
) -> AdminUser:
    """Dependency: resolve the session cookie and enforce CSRF on unsafe methods."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )

    row = await session.scalar(select(AdminSession).where(AdminSession.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None or _as_utc(row.expires_at) <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")

    if request.method.upper() in _UNSAFE_METHODS:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or _hash(csrf) != row.csrf_hash:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

    user = await session.get(AdminUser, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account disabled")
    return user


# A fixed hash keeps verification cost identical when the email is unknown.
_DUMMY_HASH = passwords.hash_password("not-a-real-password-placeholder")
