"""Smart-TV guide cap: a large guide is narrowed instead of truncated."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import (
    Assignment,
    Channel,
    ChannelEpgMap,
    Device,
    EpgChannel,
    EpgSource,
    PlaylistProfile,
    Programme,
)
from iptvcc.security import hash_token
from iptvcc.services.epg import build_guide

TOKEN = "cap-token-cap-token-cap"


async def _seed(session: AsyncSession) -> None:
    epg_source = EpgSource(name="Guide", default_timezone="UTC")
    session.add(epg_source)
    await session.flush()
    epg_channel = EpgChannel(epg_source_id=epg_source.id, source_tvg_id="a.test", display_name="A")
    session.add(epg_channel)
    channel = Channel(public_key="pk-cap", name="A", display_name="A", tvg_id="a.test")
    session.add(channel)
    await session.flush()
    session.add(ChannelEpgMap(channel_id=channel.id, epg_channel_id=epg_channel.id))

    profile = PlaylistProfile(name="Cap", is_default=True)
    device = Device(name="Cap TV")
    session.add_all([profile, device])
    await session.flush()
    session.add(
        Assignment(
            device_id=device.id,
            playlist_profile_id=profile.id,
            token_hash=hash_token(TOKEN),
            token_prefix="cap",
        )
    )

    now = int(time.time())
    # Eight days of hourly programmes: enough that a seven-day window is much larger than a cap.
    for hour in range(8 * 24):
        start = now - 86_400 + hour * 3_600
        session.add(
            Programme(
                epg_channel_id=epg_channel.id,
                start_epoch=start,
                stop_epoch=start + 3_600,
                title=f"Programme {hour}",
                description="A description long enough to make the document non-trivial. " * 2,
            )
        )
    await session.commit()


def _programmes(path: Path) -> int:
    return len(etree.parse(str(path)).getroot().findall("programme"))


@pytest.mark.asyncio
async def test_cap_narrows_the_window_and_keeps_valid_xml(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    async with session_factory() as session:
        await _seed(session)

        full = await build_guide(session, TOKEN, tmp_path, max_bytes=0)
        assert full is not None
        full_size = full[0].stat().st_size
        full_count = _programmes(full[0])

        capped = await build_guide(session, TOKEN, tmp_path, max_bytes=20_000)
        assert capped is not None
        capped_size = capped[0].stat().st_size
        capped_count = _programmes(capped[0])

        # A well-formed document, fewer programmes, and smaller than the uncapped render.
        etree.parse(str(capped[0]))
        assert capped_count < full_count
        assert capped_size < full_size
        # The narrowest window is one day, so it may still exceed a very small cap: that is
        # documented behaviour, but it must not be larger than the full render.
        assert capped_size <= max(20_000, full_size)


@pytest.mark.asyncio
async def test_cap_disabled_renders_the_full_window(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    async with session_factory() as session:
        await _seed(session)
        result = await build_guide(session, TOKEN, tmp_path, max_bytes=None)
        assert result is not None
        # 7 forward + 1 back of hourly programmes, all of them.
        assert _programmes(result[0]) > 150


@pytest.mark.asyncio
async def test_the_endpoint_serves_the_capped_guide(
    client, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch
) -> None:
    """The API must pass the configured cap through, not just the service."""
    from iptvcc.config import get_settings

    async with session_factory() as session:
        await _seed(session)
        full = await build_guide(session, TOKEN, tmp_path / "full", max_bytes=0)
        assert full is not None
        full_size = full[0].stat().st_size

    monkeypatch.setattr(get_settings(), "epg_max_bytes", 5_000, raising=False)
    response = await client.get(f"/p/{TOKEN}/epg.xml")
    assert response.status_code == 200
    assert len(response.content) < full_size
    etree.fromstring(response.content)
