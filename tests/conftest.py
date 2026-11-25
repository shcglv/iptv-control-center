"""Shared pytest fixtures: isolated database, authenticated client, anonymous client."""

from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path

# Configure the app before it is imported anywhere.
os.environ["IPTV_APP_ENV"] = "test"
os.environ["IPTV_ADMIN_API_KEY"] = "test-admin"
os.environ["IPTV_ENCRYPTION_KEY"] = base64.b64encode(b"k" * 32).decode()
os.environ["IPTV_DATABASE_URL"] = "sqlite+aiosqlite:///./data/pytest-unused.db"
os.environ["IPTV_PUBLIC_BASE_URL"] = "http://test"
os.environ["IPTV_ARTIFACTS_DIR"] = "./data/test-artifacts"
# Tests fetch their own local HTTP servers for adapters and probes.
os.environ["IPTV_ALLOW_PRIVATE_NETWORK_TARGETS"] = "1"

from collections.abc import AsyncIterator, Iterator

import httpx
import pyotp
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from iptvcc.api import app
from iptvcc.auth import create_admin_user, reset_login_rate_limit
from iptvcc.db import Base, get_session

TEST_ADMIN_EMAIL = "admin@test.local"
TEST_ADMIN_PASSWORD = "correct-horse-battery"
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"

# Mutated by the ``client`` fixture so tests can pass ``headers=ADMIN_HEADERS``.
ADMIN_HEADERS: dict[str, str] = {}

SAMPLE_M3U = """#EXTM3U url-tvg="http://epg.test/guide.xml"
#EXTINF:-1 tvg-id="bbc1.uk" tvg-name="BBC One" tvg-logo="http://logo.test/bbc1.png" group-title="News",BBC One HD
http://provider.test/live/bbc1.ts
#EXTINF:-1 tvg-id="cnn.us" tvg-logo="http://logo.test/cnn.png" group-title="News",CNN
http://provider.test/live/cnn.ts
#EXTINF:-1 group-title="Sport",Eurosport 1 [HD]
http://provider.test/live/eurosport.m3u8
#EXTINF:-1,No Attributes Channel
http://provider.test/live/plain.ts
"""


@pytest.fixture(autouse=True)
def clean_artifacts() -> Iterator[None]:
    """Rendered guides are cached on disk and keyed by profile id; ids restart per test."""
    path = Path(os.environ["IPTV_ARTIFACTS_DIR"])
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


@pytest_asyncio.fixture
async def session_factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _bind_session(factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    # The scheduler tick endpoint resolves its factory from app.state so tests can redirect it.
    app.state.session_factory = factory


@pytest_asyncio.fixture
async def anon_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """Unauthenticated client (no cookie, no CSRF token)."""
    _bind_session(session_factory)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """Authenticated client: an admin exists, is logged in, and CSRF is set."""
    reset_login_rate_limit()
    async with session_factory() as session:
        await create_admin_user(
            session,
            email=TEST_ADMIN_EMAIL,
            password=TEST_ADMIN_PASSWORD,
            totp_secret=TEST_TOTP_SECRET,
        )
        await session.commit()

    _bind_session(session_factory)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            code = pyotp.TOTP(TEST_TOTP_SECRET).now()
            response = await http_client.post(
                "/api/v1/auth/login",
                json={
                    "email": TEST_ADMIN_EMAIL,
                    "password": TEST_ADMIN_PASSWORD,
                    "totp": code,
                },
            )
            assert response.status_code == 200, response.text
            ADMIN_HEADERS.clear()
            ADMIN_HEADERS["X-CSRF-Token"] = response.json()["csrf_token"]
            yield http_client
    finally:
        ADMIN_HEADERS.clear()
        app.dependency_overrides.pop(get_session, None)
