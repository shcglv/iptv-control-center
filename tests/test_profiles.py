"""Playlist profiles: visibility, ordering, category rules and pinned sources."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import (
    Category,
    Channel,
    ChannelStream,
    PlaylistProfile,
    ProfileCategory,
    ProfileChannel,
    Source,
    Stream,
    StreamQuality,
)
from iptvcc.secrets import encrypt
from iptvcc.services.delivery import resolve_stream
from iptvcc.services.profiles import visible_channels

from .conftest import ADMIN_HEADERS, SAMPLE_M3U


async def _catalog(session: AsyncSession) -> tuple[PlaylistProfile, dict[str, Channel]]:
    news = Category(name="News", sort_index=0)
    sport = Category(name="Sport", sort_index=1)
    session.add_all([news, sport])
    await session.flush()

    channels = {
        "bbc": Channel(
            public_key="pk-bbc",
            name="BBC",
            display_name="BBC One HD",
            number=1,
            category_id=news.id,
        ),
        "cnn": Channel(
            public_key="pk-cnn", name="CNN", display_name="CNN", number=2, category_id=news.id
        ),
        "euro": Channel(
            public_key="pk-euro",
            name="Eurosport",
            display_name="Eurosport 1",
            number=3,
            category_id=sport.id,
        ),
    }
    session.add_all(list(channels.values()))
    profile = PlaylistProfile(name="Default", is_default=True)
    session.add(profile)
    await session.flush()
    return profile, channels


# --- service -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_everything_visible_by_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        profile, _channels = await _catalog(session)
        lineup = await visible_channels(session, profile.id)
        await session.commit()
        assert [item.channel.number for item in lineup] == [1, 2, 3]


@pytest.mark.asyncio
async def test_hidden_channel_is_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        profile, channels = await _catalog(session)
        session.add(
            ProfileChannel(profile_id=profile.id, channel_id=channels["cnn"].id, visible=False)
        )
        await session.commit()
        lineup = await visible_channels(session, profile.id)
        assert [item.channel.display_name for item in lineup] == ["BBC One HD", "Eurosport 1"]


@pytest.mark.asyncio
async def test_hidden_category_hides_members_but_channel_override_wins(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        profile, channels = await _catalog(session)
        session.add(
            ProfileCategory(
                profile_id=profile.id, category_id=channels["bbc"].category_id or 0, visible=False
            )
        )
        session.add(
            ProfileChannel(profile_id=profile.id, channel_id=channels["bbc"].id, visible=True)
        )
        await session.commit()
        lineup = await visible_channels(session, profile.id)
        # Both news channels are hidden by the category, but BBC is explicitly re-enabled.
        assert [item.channel.display_name for item in lineup] == ["BBC One HD", "Eurosport 1"]


@pytest.mark.asyncio
async def test_sort_index_reorders_the_lineup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        profile, channels = await _catalog(session)
        session.add(
            ProfileChannel(
                profile_id=profile.id, channel_id=channels["euro"].id, visible=True, sort_index=0
            )
        )
        await session.commit()
        lineup = await visible_channels(session, profile.id)
        assert next(item.channel.display_name for item in lineup) == "Eurosport 1"


@pytest.mark.asyncio
async def test_category_display_name_renames_group(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        profile, channels = await _catalog(session)
        session.add(
            ProfileCategory(
                profile_id=profile.id,
                category_id=channels["bbc"].category_id or 0,
                visible=True,
                display_name="World News",
            )
        )
        await session.commit()
        lineup = await visible_channels(session, profile.id)
        names = {item.channel.display_name: item.group_name for item in lineup}
        assert names["BBC One HD"] == "World News"


# --- pinned source -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_source_overrides_quality(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        source = Source(name="S", url="http://p.test/l.m3u")
        session.add(source)
        await session.flush()
        good = Stream(
            source_id=source.id,
            stream_key="good",
            stream_hash="h-good",
            name="Good",
            url_enc=encrypt("http://good.test/1.ts"),
            container="ts",
        )
        bad = Stream(
            source_id=source.id,
            stream_key="bad",
            stream_hash="h-bad",
            name="Bad",
            url_enc=encrypt("http://bad.test/1.ts"),
            container="ts",
        )
        session.add_all([good, bad])
        await session.flush()
        channel = Channel(public_key="pk-pin", name="Pin", display_name="Pin", number=1)
        session.add(channel)
        await session.flush()
        session.add_all(
            [
                ChannelStream(channel_id=channel.id, stream_id=good.id, priority=0),
                ChannelStream(channel_id=channel.id, stream_id=bad.id, priority=1),
            ]
        )
        session.add_all(
            [
                StreamQuality(
                    stream_id=good.id,
                    checks=10,
                    failures=0,
                    uptime_ratio=1.0,
                    p90_ttfb_ms=200,
                    resolution="1920x1080",
                ),
                StreamQuality(
                    stream_id=bad.id,
                    checks=10,
                    failures=8,
                    uptime_ratio=0.2,
                    p90_ttfb_ms=3000,
                    resolution="1280x720",
                ),
            ]
        )
        profile = PlaylistProfile(name="Pinned")
        session.add(profile)
        await session.flush()
        session.add(
            ProfileChannel(profile_id=profile.id, channel_id=channel.id, pinned_stream_id=bad.id)
        )
        await session.commit()

        resolved = await resolve_stream(session, "pk-pin", "ts", profile_id=profile.id)
        assert resolved is not None
        assert resolved.url == "http://bad.test/1.ts"

        # Without a profile pin the healthy stream wins.
        unpinned = await resolve_stream(session, "pk-pin", "ts")
        assert unpinned is not None
        assert unpinned.url == "http://good.test/1.ts"


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_api_controls_the_playlist(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "M3U", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )

    profile = (
        await client.post("/api/v1/profiles", json={"name": "Living Room"}, headers=ADMIN_HEADERS)
    ).json()
    assert profile["channels_visible"] == 4

    channels = (
        await client.get(f"/api/v1/profiles/{profile['id']}/channels", headers=ADMIN_HEADERS)
    ).json()
    by_name = {row["name"]: row for row in channels}
    cnn = by_name["CNN"]

    hidden = await client.put(
        f"/api/v1/profiles/{profile['id']}/channels/{cnn['public_key']}",
        json={"visible": False},
        headers=ADMIN_HEADERS,
    )
    assert hidden.status_code == 200
    assert hidden.json()["visible"] is False

    device = (
        await client.post(
            "/api/v1/devices",
            json={"name": "TV", "profile_id": profile["id"]},
            headers=ADMIN_HEADERS,
        )
    ).json()
    token = device["token"]

    playlist = (await client.get(f"/p/{token}/live.m3u")).text
    assert "CNN" not in playlist
    assert "BBC One HD" in playlist

    # Sorting: move Eurosport to the top of the playlist.
    euro = by_name["Eurosport 1 [HD]"]
    await client.put(
        f"/api/v1/profiles/{profile['id']}/channels/{euro['public_key']}",
        json={"visible": True, "sort_index": 0},
        headers=ADMIN_HEADERS,
    )
    reordered = (await client.get(f"/p/{token}/live.m3u")).text
    names = [line.split(",", 1)[1] for line in reordered.splitlines() if line.startswith("#EXTINF")]
    assert names[0].startswith("Eurosport")

    # Removing the override restores the catalog entry.
    cleared = await client.delete(
        f"/api/v1/profiles/{profile['id']}/channels/{cnn['public_key']}", headers=ADMIN_HEADERS
    )
    assert cleared.status_code == 204
    restored = (await client.get(f"/p/{token}/live.m3u")).text
    assert "CNN" in restored


@pytest.mark.asyncio
async def test_profile_hiding_also_hides_the_guide(
    client: httpx.AsyncClient,
) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "M3U", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    epg_source = (
        await client.post(
            "/api/v1/epg/sources",
            json={"name": "Guide", "default_timezone": "UTC"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(microsecond=0)
    fmt = lambda moment: moment.strftime("%Y%m%d%H%M%S +0000")  # noqa: E731
    guide = f"""<?xml version="1.0"?>
<tv><channel id="cnn.us"><display-name>CNN</display-name></channel>
<programme start="{fmt(now)}" stop="{fmt(now + timedelta(hours=1))}" channel="cnn.us">
<title>CNN Hour</title></programme></tv>"""
    await client.post(
        f"/api/v1/epg/sources/{epg_source['id']}/import",
        json={"xml": guide},
        headers=ADMIN_HEADERS,
    )

    profile = (
        await client.post("/api/v1/profiles", json={"name": "No CNN"}, headers=ADMIN_HEADERS)
    ).json()
    channels = (
        await client.get(f"/api/v1/profiles/{profile['id']}/channels", headers=ADMIN_HEADERS)
    ).json()
    cnn = next(row for row in channels if row["name"] == "CNN")
    await client.put(
        f"/api/v1/profiles/{profile['id']}/channels/{cnn['public_key']}",
        json={"visible": False},
        headers=ADMIN_HEADERS,
    )

    device = (
        await client.post(
            "/api/v1/devices",
            json={"name": "TV2", "profile_id": profile["id"]},
            headers=ADMIN_HEADERS,
        )
    ).json()
    response = await client.get(f"/p/{device['token']}/epg.xml")
    assert response.status_code == 200
    assert b'id="cnn.us"' not in response.content


@pytest.mark.asyncio
async def test_profile_order_endpoint_renumbers_in_one_request(
    client: httpx.AsyncClient,
) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "M3U", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    profile = (
        await client.post("/api/v1/profiles", json={"name": "Order"}, headers=ADMIN_HEADERS)
    ).json()
    channels = (
        await client.get(f"/api/v1/profiles/{profile['id']}/channels", headers=ADMIN_HEADERS)
    ).json()
    reversed_keys = [row["public_key"] for row in reversed(channels)]

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/order",
        json={"channels": reversed_keys},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 204, response.text

    device = (
        await client.post(
            "/api/v1/devices",
            json={"name": "Order TV", "profile_id": profile["id"]},
            headers=ADMIN_HEADERS,
        )
    ).json()
    playlist = (await client.get(f"/p/{device['token']}/live.m3u")).text
    names = [line.split(",", 1)[1] for line in playlist.splitlines() if line.startswith("#EXTINF")]
    assert names == [row["name"] for row in reversed(channels)]

    unknown = await client.post(
        f"/api/v1/profiles/{profile['id']}/order",
        json={"channels": ["pk-does-not-exist"]},
        headers=ADMIN_HEADERS,
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_profile_api_validation(client: httpx.AsyncClient) -> None:
    first = await client.post("/api/v1/profiles", json={"name": "Dup"}, headers=ADMIN_HEADERS)
    assert first.status_code == 201
    second = await client.post("/api/v1/profiles", json={"name": "Dup"}, headers=ADMIN_HEADERS)
    assert second.status_code == 409

    missing = await client.put(
        "/api/v1/profiles/999/channels/pk-nope", json={"visible": False}, headers=ADMIN_HEADERS
    )
    assert missing.status_code == 404
