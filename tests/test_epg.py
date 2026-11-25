"""EPG: XMLTV parsing, ingestion, mapping and the per-device guide endpoint."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import Channel, EpgSource, Programme
from iptvcc.services.epg import (
    XmltvError,
    ingest_xmltv,
    iter_xmltv,
    map_channels,
    parse_xmltv_time,
)

from .conftest import ADMIN_HEADERS, SAMPLE_M3U


def test_iter_xmltv_accepts_str_and_bytes() -> None:
    """Both call styles must work: the API passes a decoded string, imports pass bytes."""
    document = (
        '<?xml version="1.0"?><tv><channel id="a.test"><display-name>A</display-name></channel>'
        '<programme start="20261124160000 +0000" stop="20261124170000 +0000" channel="a.test">'
        "<title>T</title></programme></tv>"
    )
    from_str = list(iter_xmltv(document, "UTC"))
    from_bytes = list(iter_xmltv(document.encode("utf-8"), "UTC"))
    assert [kind for kind, _ in from_str] == ["channel", "programme"]
    assert from_str == from_bytes


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y%m%d%H%M%S +0000")


def _guide(*, past: bool = False) -> str:
    """Build an XMLTV document with times relative to now (tests never rot)."""
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(hours=2) if not past else now - timedelta(days=30)
    stop = start + timedelta(hours=1)
    later = now + timedelta(hours=1)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="tests">
  <channel id="guide.bbc1"><display-name>BBC One HD</display-name><icon src="http://logo/bbc.png"/></channel>
  <channel id="cnn.us"><display-name>CNN</display-name></channel>
  <programme start="{_fmt(start)}" stop="{_fmt(stop)}" channel="guide.bbc1">
    <title>Morning News</title><desc>Headlines</desc><category>News</category>
  </programme>
  <programme start="{_fmt(later)}" stop="{_fmt(later + timedelta(hours=1))}" channel="guide.bbc1">
    <title>Later Show</title>
  </programme>
  <programme start="{_fmt(start)}" stop="{_fmt(stop)}" channel="cnn.us">
    <title>CNN Hour</title><sub-title>Live</sub-title>
  </programme>
</tv>
"""


# --- parsing -----------------------------------------------------------------------------------


def test_parse_xmltv_time_with_offset() -> None:
    expected = int(datetime(2026, 11, 25, 12, 0, tzinfo=UTC).timestamp())
    assert parse_xmltv_time("20261125120000 +0000", "UTC") == expected
    # 12:00+03:00 is 09:00 UTC
    assert parse_xmltv_time("20261125120000 +0300", "UTC") == expected - 3 * 3600
    # 12:00-05:00 is 17:00 UTC
    assert parse_xmltv_time("20261125120000 -0500", "UTC") == expected + 5 * 3600


def test_parse_xmltv_time_without_offset_uses_source_timezone() -> None:
    moscow = parse_xmltv_time("20261125120000", "Europe/Moscow")
    assert moscow == int(datetime(2026, 11, 25, 9, 0, tzinfo=UTC).timestamp())


def test_parse_xmltv_time_rejects_garbage() -> None:
    with pytest.raises(XmltvError):
        parse_xmltv_time("not-a-date", "UTC")


# --- ingestion ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_maps_channels_and_stores_programmes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        source = EpgSource(name="Guide", default_timezone="UTC", retention_days=3)
        session.add(source)
        bbc = Channel(
            public_key="pk-bbc",
            name="BBC One",
            display_name="BBC One HD",
            number=1,
            tvg_id="bbc1.uk",
        )
        session.add(bbc)
        await session.flush()

        stats = await ingest_xmltv(session, source, _guide().encode("utf-8"))
        await session.commit()

        assert stats.channels == 2
        assert stats.programmes == 3
        # "guide.bbc1" is not the tvg-id, so BBC is matched by normalised display name.
        assert stats.mapped == 1
        stored = await session.scalar(select(func.count()).select_from(Programme))
        assert stored == 3


@pytest.mark.asyncio
async def test_reingest_is_idempotent(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        source = EpgSource(name="Guide", default_timezone="UTC")
        session.add(source)
        await session.flush()

        await ingest_xmltv(session, source, _guide().encode("utf-8"))
        await session.commit()
        first = await session.scalar(select(func.count()).select_from(Programme))

        await ingest_xmltv(session, source, _guide().encode("utf-8"))
        await session.commit()
        second = await session.scalar(select(func.count()).select_from(Programme))
        assert first == 3
        assert second == 3


@pytest.mark.asyncio
async def test_retention_prunes_old_programmes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        source = EpgSource(name="Guide", default_timezone="UTC", retention_days=1)
        session.add(source)
        await session.flush()

        stats = await ingest_xmltv(session, source, _guide(past=True).encode("utf-8"))
        await session.commit()
        # Two programmes are in the past (pruned); the future one is kept.
        assert stats.pruned == 2
        assert stats.programmes == 1


@pytest.mark.asyncio
async def test_doctype_is_rejected(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        source = EpgSource(name="Guide")
        session.add(source)
        await session.flush()
        with pytest.raises(XmltvError, match="DOCTYPE"):
            await ingest_xmltv(
                session,
                source,
                b'<?xml version="1.0"?><!DOCTYPE tv SYSTEM "http://evil/x.dtd"><tv/>',
            )


@pytest.mark.asyncio
async def test_map_channels_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        source = EpgSource(name="Guide", default_timezone="UTC")
        session.add(source)
        session.add(
            Channel(public_key="pk1", name="BBC One", display_name="BBC One HD", tvg_id="bbc1.uk")
        )
        await session.flush()
        await ingest_xmltv(session, source, _guide().encode("utf-8"))
        first = await map_channels(session)
        second = await map_channels(session)
        await session.commit()
        assert first == 1
        assert second == 1


# --- delivery -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_guide_endpoint_joins_with_playlist(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "M3U", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import",
        json={"m3u": SAMPLE_M3U},
        headers=ADMIN_HEADERS,
    )

    epg_source = (
        await client.post(
            "/api/v1/epg/sources",
            json={"name": "Guide", "default_timezone": "UTC", "retention_days": 3},
            headers=ADMIN_HEADERS,
        )
    ).json()
    imported = await client.post(
        f"/api/v1/epg/sources/{epg_source['id']}/import",
        json={"xml": _guide()},
        headers=ADMIN_HEADERS,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["stats"]["programmes"] == 3

    status = (await client.get("/api/v1/epg/status", headers=ADMIN_HEADERS)).json()
    assert status["epg_channels"] == 2
    assert status["mapped_channels"] >= 2

    device = (
        await client.post("/api/v1/devices", json={"name": "TV"}, headers=ADMIN_HEADERS)
    ).json()
    token = device["token"]

    playlist = (await client.get(f"/p/{token}/live.m3u")).text
    # BBC is mapped by name to the guide's id, so the playlist must carry the guide id.
    assert 'tvg-id="guide.bbc1"' in playlist
    assert 'tvg-id="cnn.us"' in playlist

    guide = await client.get(f"/p/{token}/epg.xml")
    assert guide.status_code == 200
    assert guide.headers["content-type"].startswith("application/xml")
    root = etree.fromstring(guide.content)
    channel_ids = [element.get("id") for element in root.findall("channel")]
    assert "guide.bbc1" in channel_ids
    programme_channels = {element.get("channel") for element in root.findall("programme")}
    assert programme_channels == {"guide.bbc1", "cnn.us"}

    compressed = await client.get(f"/p/{token}/epg.xml.gz")
    assert compressed.status_code == 200
    gz_root = etree.fromstring(gzip.decompress(compressed.content))
    assert gz_root.tag == "tv"


@pytest.mark.asyncio
async def test_device_guide_rejects_unknown_token(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get("/p/ipt_nope/epg.xml")).status_code == 404
