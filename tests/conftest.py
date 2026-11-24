"""Shared pytest fixtures: isolated database, app client, seeded data."""

from __future__ import annotations

import base64
import os

# Configure the app before it is imported anywhere.
os.environ["IPTV_APP_ENV"] = "test"
os.environ["IPTV_ADMIN_API_KEY"] = "test-admin"
os.environ["IPTV_ENCRYPTION_KEY"] = base64.b64encode(b"k" * 32).decode()
os.environ["IPTV_DATABASE_URL"] = "sqlite+aiosqlite:///./data/pytest-unused.db"
os.environ["IPTV_PUBLIC_BASE_URL"] = "http://test"

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from iptvcc.api import app
from iptvcc.db import Base, get_session

ADMIN_HEADERS = {"X-Admin-Token": "test-admin"}

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


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
