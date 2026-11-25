"""Real-world integration checks against the public internet.

Opt-in, because they depend on third-party data that changes without warning:

    $env:IPTV_NETWORK_TESTS = "1"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_real_world.py -q

They assert the *pipeline* works on real data (a real M3U is fetched, parsed, probed and resolved),
not that any particular public stream is alive — providers rotate and geo-block them.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import Assignment, Category, Channel, Device, PlaylistProfile, Source, Stream
from iptvcc.netguard import UnsafeUrlError, fetch_text
from iptvcc.secrets import decrypt
from iptvcc.security import hash_token
from iptvcc.services.delivery import build_playlist, resolve_stream
from iptvcc.services.health import probe_url
from iptvcc.services.importing import import_playlist
from iptvcc.services.selector import rank_candidates

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("IPTV_NETWORK_TESTS") != "1",
        reason="set IPTV_NETWORK_TESTS=1 to run checks that need the internet",
    ),
]

NEWS_PLAYLIST = "https://iptv-org.github.io/iptv/categories/news.m3u"


@pytest_asyncio.fixture
async def imported(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AsyncSession, Source]]:
    async with session_factory() as session:
        try:
            body = await fetch_text(NEWS_PLAYLIST, timeout=30)
        except (UnsafeUrlError, httpx.HTTPError) as exc:
            pytest.skip(f"could not fetch the public playlist: {exc}")
        source = Source(name="iptv-org news", url=NEWS_PLAYLIST)
        session.add(source)
        await session.flush()
        await import_playlist(session, source, body)
        await session.commit()
        yield session, source


@pytest.mark.asyncio
async def test_a_real_public_playlist_imports(imported: tuple[AsyncSession, Source]) -> None:
    session, source = imported
    channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
    streams = await session.scalar(select(func.count()).select_from(Stream)) or 0
    groups = await session.scalar(select(func.count()).select_from(Category)) or 0

    # The public news list is around a thousand entries; a big change means a parsing regression.
    assert channels > 100, channels
    assert streams >= channels, (channels, streams)
    assert groups > 5, groups
    assert source.stream_count > 100


@pytest.mark.asyncio
async def test_real_streams_are_reachable_and_describe_their_format(
    imported: tuple[AsyncSession, Source],
) -> None:
    session, _source = imported
    rows = (await session.scalars(select(Stream).where(Stream.is_stale.is_(False)).limit(12))).all()

    outcomes = []
    for row in rows:
        outcomes.append(await probe_url(decrypt(row.url_enc), timeout=10))

    reachable = [outcome for outcome in outcomes if outcome.ok]
    if not reachable:
        pytest.skip("no public stream answered from this network right now")

    ttfb = [outcome.ttfb_ms for outcome in reachable if outcome.ttfb_ms is not None]
    assert statistics.median(ttfb) < 10_000
    # At least one HLS master playlist should describe its resolution.
    assert any(outcome.resolution for outcome in reachable)


@pytest.mark.asyncio
async def test_resolver_points_at_a_real_provider_url(
    imported: tuple[AsyncSession, Source],
) -> None:
    session, _source = imported
    profile = PlaylistProfile(name="Real", is_default=True)
    device = Device(name="Real TV")
    session.add_all([profile, device])
    await session.flush()
    session.add(
        Assignment(
            device_id=device.id,
            playlist_profile_id=profile.id,
            token_hash=hash_token("real-token-real-token"),
            token_prefix="real",
        )
    )
    await session.commit()

    playlist = await build_playlist(session, "real-token-real-token", "http://127.0.0.1:8000")
    assert playlist is not None
    assert playlist.count("#EXTINF") > 100

    streams = (await session.scalars(select(Stream).limit(12))).all()
    checked = 0
    for stream in streams:
        channel = None
        for candidate in (await session.scalars(select(Channel).limit(200))).all():
            ranked = await rank_candidates(session, candidate, stream.container)
            if any(item.stream_id == stream.id for item in ranked):
                channel = candidate
                break
        if channel is None:
            continue
        target = await resolve_stream(session, channel.public_key, stream.container)
        if target is None:
            continue
        assert target.url.startswith(("http://", "https://"))
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
            try:
                response = await client.head(target.url)
            except httpx.HTTPError:
                continue
        checked += 1
        assert response.status_code < 500 or response.status_code in {403, 404}
        if checked >= 2:
            break

    if checked == 0:
        pytest.skip("no resolvable channel answered from this network right now")
