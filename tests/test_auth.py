"""Admin authentication: password + TOTP, sessions, CSRF, logout."""

from __future__ import annotations

import httpx
import pyotp
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc import passwords, totp

from .conftest import ADMIN_HEADERS, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD, TEST_TOTP_SECRET


def _totp() -> str:
    return pyotp.TOTP(TEST_TOTP_SECRET).now()


# --- units -------------------------------------------------------------------------------------


def test_password_hash_roundtrip() -> None:
    hashed = passwords.hash_password("correct-horse-battery")
    assert hashed != "correct-horse-battery"
    assert passwords.verify_password("correct-horse-battery", hashed)
    assert not passwords.verify_password("wrong", hashed)


def test_password_minimum_length() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        passwords.hash_password("short")


def test_totp_verify_window() -> None:
    secret = totp.new_secret()
    code = pyotp.TOTP(secret).now()
    assert totp.verify(secret, code)
    assert not totp.verify(secret, "000000")
    assert not totp.verify(secret, "abc")


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_grants_access(client: httpx.AsyncClient) -> None:
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == TEST_ADMIN_EMAIL


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": TEST_ADMIN_EMAIL, "password": "wrong-password-value", "totp": _totp()},
    )
    assert response.status_code == 401
    assert "set-cookie" not in {key.lower() for key in response.headers}


@pytest.mark.asyncio
async def test_login_rejects_wrong_totp(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD, "totp": "000000"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_unknown_email(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.local", "password": TEST_ADMIN_PASSWORD, "totp": _totp()},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_cookie_attributes(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD, "totp": _totp()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["csrf_token"]
    assert body["expires_in"] > 0
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie


@pytest.mark.asyncio
async def test_unsafe_request_without_csrf_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/sources", json={"name": "NoCsrf", "url": ""})
    assert response.status_code == 403
    assert "csrf" in response.text.lower()


@pytest.mark.asyncio
async def test_unsafe_request_with_wrong_csrf_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/sources",
        json={"name": "BadCsrf", "url": ""},
        headers={"X-CSRF-Token": "not-the-real-token"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_logout_flow(client: httpx.AsyncClient) -> None:
    csrf = ADMIN_HEADERS["X-CSRF-Token"]
    logout = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_revoked_session_token_cannot_be_replayed(client: httpx.AsyncClient) -> None:
    cookie = next(c for c in client.cookies.jar if c.name == "iptv_session")
    token = cookie.value
    csrf = ADMIN_HEADERS["X-CSRF-Token"]

    assert (
        await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    ).status_code == 204

    # Put the revoked token back: the server must reject it on its own merits.
    client.cookies.set("iptv_session", token)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_login_reports_failure_when_the_session_cannot_be_saved(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A login that cannot persist its session must not answer 200.

    FastAPI finalises a yield dependency after the response is built, so a failing commit there is
    invisible to the client. The login route commits explicitly and answers 503 instead.
    """
    import pyotp

    from iptvcc.api import app
    from iptvcc.auth import create_admin_user
    from iptvcc.db import get_session

    email = "commit-failure@test.local"
    async with session_factory() as session:
        await create_admin_user(
            session, email=email, password=TEST_ADMIN_PASSWORD, totp_secret=TEST_TOTP_SECRET
        )
        await session.commit()

    async def broken_session():
        async with session_factory() as session:
            real_commit = session.commit

            async def boom() -> None:
                raise RuntimeError("disk gone")

            session.commit = boom  # type: ignore[method-assign]
            try:
                yield session
            finally:
                session.commit = real_commit

    app.dependency_overrides[get_session] = broken_session
    try:
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": TEST_ADMIN_PASSWORD,
                "totp": pyotp.TOTP(TEST_TOTP_SECRET).now(),
            },
        )
        assert response.status_code == 503, response.text
        assert "session" in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)
