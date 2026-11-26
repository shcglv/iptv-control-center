"""System information and the in-panel preflight."""

from __future__ import annotations

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import iptvcc

from .conftest import ADMIN_HEADERS


def _head() -> str:
    return ScriptDirectory.from_config(Config("alembic.ini")).get_current_head() or ""


async def _record_migration(
    session_factory: async_sessionmaker[AsyncSession], version: str
) -> None:
    async with session_factory() as session:
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL)")
        )
        await session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version}
        )
        await session.commit()


@pytest.mark.asyncio
async def test_system_info_reports_version_and_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/system/info", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == iptvcc.__version__
    assert body["database"] == "sqlite"
    assert body["channels"] == 0
    assert body["profiles"] == 0


@pytest.mark.asyncio
async def test_system_check_passes_when_the_schema_is_current(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _record_migration(session_factory, _head())

    response = await client.get("/api/v1/system/check", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    checks = {row["name"]: row for row in response.json()}

    assert len(checks) == 8
    assert checks["encryption key"]["status"] == "ok"
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"]["status"] == "ok"
    assert checks["migrations"]["detail"] == _head()


@pytest.mark.asyncio
async def test_system_check_fails_on_an_unmigrated_database(
    client: httpx.AsyncClient,
) -> None:
    # No alembic_version row in a fresh test schema: the panel must say so, not pretend.
    response = await client.get("/api/v1/system/check", headers=ADMIN_HEADERS)
    checks = {row["name"]: row for row in response.json()}
    assert checks["migrations"]["status"] == "fail"
    assert checks["database"]["status"] == "ok"


@pytest.mark.asyncio
async def test_system_endpoints_require_admin(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get("/api/v1/system/info")).status_code == 401
    assert (await anon_client.get("/api/v1/system/check")).status_code == 401
