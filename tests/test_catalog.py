"""Channel identity catalog, matching and take-down blocks."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import Channel, Source
from iptvcc.services.catalog import (
    apply_blocklist,
    catalog_status,
    ingest_catalog,
    match_channels,
)
from iptvcc.services.importing import import_playlist

from .conftest import ADMIN_HEADERS, SAMPLE_M3U

CATALOG = [
    {
        "id": "BBCOne.uk",
        "name": "BBC One",
        "alt_names": ["BBC 1"],
        "country": "GB",
        "categories": ["news"],
    },
    {"id": "BBCOne.us", "name": "BBC One", "country": "US"},
    {"id": "CNN.us", "name": "CNN", "country": "US"},
    {"id": "Eurosport1.uk", "name": "Eurosport", "alt_names": ["Eurosport 1"], "country": "GB"},
]


async def _import(session: AsyncSession) -> dict[str, Channel]:
    source = Source(name="P", url="http://p.test/l.m3u")
    session.add(source)
    await session.flush()
    playlist = (
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-id="bbc1.uk",BBC One HD\nhttp://p.test/bbc.ts\n'
        "#EXTINF:-1,CNN\nhttp://p.test/cnn.ts\n"
        "#EXTINF:-1,Eurosport 1\nhttp://p.test/euro.ts\n"
        "#EXTINF:-1,Unknown Channel\nhttp://p.test/unknown.ts\n"
    )
    await import_playlist(session, source, playlist)
    await session.flush()
    channels = (await session.scalars(select(Channel))).all()
    return {channel.display_name: channel for channel in channels}


# --- ingest ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_upserts_by_catalog_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first = await ingest_catalog(session, CATALOG)
        await session.commit()
        assert first.created == 4
        assert first.updated == 0

        second = await ingest_catalog(session, [{**CATALOG[0], "name": "BBC One HD"}])
        await session.commit()
        assert second.updated == 1
        assert second.created == 0

        status = await catalog_status(session)
        assert status["catalog_channels"] == 4


@pytest.mark.asyncio
async def test_ingest_skips_rows_without_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        stats = await ingest_catalog(session, [{"name": "no id"}, {"id": "x", "name": ""}])
        await session.commit()
        assert stats.skipped == 2
        assert stats.created == 0


# --- matching ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matching_uses_names_aliases_and_country(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ingest_catalog(session, CATALOG)
        await _import(session)
        stats = await match_channels(session)
        await session.commit()

        channels = {row.display_name: row for row in (await session.scalars(select(Channel))).all()}
        # Name match ("BBC One HD" -> "BBC One"), country tie-break: uk -> GB, not US.
        assert channels["BBC One HD"].catalog_id == "BBCOne.uk"
        # Exact name match.
        assert channels["CNN"].catalog_id == "CNN.us"
        # Alias match.
        assert channels["Eurosport 1"].catalog_id == "Eurosport1.uk"
        # Unknown stays unmatched.
        assert channels["Unknown Channel"].catalog_id is None
        assert stats.matched == 3
        assert stats.unmatched == 1


@pytest.mark.asyncio
async def test_matching_fills_missing_tvg_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ingest_catalog(session, CATALOG)
        channels = await _import(session)
        assert channels["CNN"].tvg_id is None
        await match_channels(session)
        await session.commit()
        assert channels["CNN"].tvg_id == "CNN.us"


@pytest.mark.asyncio
async def test_matching_is_idempotent(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await ingest_catalog(session, CATALOG)
        await _import(session)
        await match_channels(session)
        await session.commit()
        second = await match_channels(session)
        await session.commit()
        assert second.matched == 0
        assert second.already == 3
        assert second.unmatched == 1


# --- blocks ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_block_hides_the_channel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from iptvcc.models import CatalogBlock

    async with session_factory() as session:
        await ingest_catalog(session, CATALOG)
        channels = await _import(session)
        await match_channels(session)
        session.add(CatalogBlock(kind="catalog_id", value="BBCOne.uk", reason="dmca"))
        await session.flush()

        applied = await apply_blocklist(session)
        await session.commit()
        assert applied["channels_hidden"] == 1
        assert channels["BBC One HD"].is_hidden is True
        assert channels["CNN"].is_hidden is False


@pytest.mark.asyncio
async def test_domain_block_disables_the_source(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from iptvcc.models import CatalogBlock

    async with session_factory() as session:
        source = Source(name="Blocked provider", url="http://blocked.test/list.m3u")
        session.add(source)
        await session.flush()
        session.add(CatalogBlock(kind="domain", value="blocked.test"))
        await session.flush()

        applied = await apply_blocklist(session)
        await session.commit()
        assert applied["sources_disabled"] == 1
        await session.refresh(source)
        assert source.enabled is False


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_api_flow(client: httpx.AsyncClient) -> None:
    imported = await client.post(
        "/api/v1/catalog/import", json={"channels": CATALOG}, headers=ADMIN_HEADERS
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] == 4

    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "P", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )

    matched = await client.post("/api/v1/catalog/match", headers=ADMIN_HEADERS)
    assert matched.status_code == 200, matched.text
    assert matched.json()["matched"] >= 1

    status = (await client.get("/api/v1/catalog/status", headers=ADMIN_HEADERS)).json()
    assert status["catalog_channels"] == 4
    assert status["matched_channels"] >= 1

    block = await client.post(
        "/api/v1/blocklist",
        json={"kind": "catalog_id", "value": "BBCOne.uk", "reason": "test"},
        headers=ADMIN_HEADERS,
    )
    assert block.status_code == 201, block.text
    duplicate = await client.post(
        "/api/v1/blocklist",
        json={"kind": "catalog_id", "value": "BBCOne.uk"},
        headers=ADMIN_HEADERS,
    )
    assert duplicate.status_code == 409

    listed = (await client.get("/api/v1/blocklist", headers=ADMIN_HEADERS)).json()
    assert len(listed) == 1

    assert (
        await client.delete(f"/api/v1/blocklist/{listed[0]['id']}", headers=ADMIN_HEADERS)
    ).status_code == 204
    assert (await client.get("/api/v1/blocklist", headers=ADMIN_HEADERS)).json() == []


@pytest.mark.asyncio
async def test_blocked_channel_does_not_resolve(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/catalog/import", json={"channels": CATALOG}, headers=ADMIN_HEADERS)
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "P", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    await client.post("/api/v1/catalog/match", headers=ADMIN_HEADERS)

    device = (
        await client.post(
            "/api/v1/devices", json={"name": "TV", "kind": "desktop"}, headers=ADMIN_HEADERS
        )
    ).json()
    playlist = (await client.get(f"/p/{device['token']}/live.m3u")).text
    assert "BBC One HD" in playlist

    await client.post(
        "/api/v1/blocklist",
        json={"kind": "catalog_id", "value": "BBCOne.uk"},
        headers=ADMIN_HEADERS,
    )

    # Hidden channel disappears from the playlist and stops resolving.
    after = (await client.get(f"/p/{device['token']}/live.m3u")).text
    assert "BBC One HD" not in after
    keys = [line for line in after.splitlines() if "/s/" in line]
    assert keys
