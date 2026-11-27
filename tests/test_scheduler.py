"""Scheduler: lease exclusivity, tier intervals, tick behaviour and the watchdog."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import (
    Channel,
    ChannelStream,
    SchedulerState,
    Source,
    Stream,
    StreamQuality,
)
from iptvcc.secrets import encrypt
from iptvcc.services.scheduler import (
    ProbeScheduler,
    acquire_lease,
    due_stream_ids,
    scheduler_status,
)

from .conftest import ADMIN_HEADERS

MASTER = (
    b"#EXTM3U\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n'
    b"v.m3u8\n"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(MASTER)))
        self.end_headers()
        self.wfile.write(MASTER)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def origin() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


async def _stream(session: AsyncSession, *, name: str, url: str = "http://p.test/1.m3u8") -> Stream:
    source = await session.scalar(select(Source).limit(1))
    if source is None:
        source = Source(name="S", url="http://p.test/l.m3u")
        session.add(source)
        await session.flush()
    stream = Stream(
        source_id=source.id,
        stream_key=name,
        stream_hash=f"hash-{name}",
        name=name,
        url_enc=encrypt(url),
        container="m3u8",
    )
    session.add(stream)
    await session.flush()
    return stream


# --- lease -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lease_is_exclusive(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as first, session_factory() as second:
        assert await acquire_lease(first, "owner-a", ttl_seconds=60) is True
        await first.commit()
        assert await acquire_lease(second, "owner-b", ttl_seconds=60) is False
        await second.rollback()

    async with session_factory() as third:
        state = await third.get(SchedulerState, 1)
        assert state is not None
        state.locked_until = int(time.time()) - 1  # lease expired
        await third.commit()

    async with session_factory() as fourth:
        assert await acquire_lease(fourth, "owner-b", ttl_seconds=60) is True


@pytest.mark.asyncio
async def test_owner_can_renew_its_own_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await acquire_lease(session, "owner-a", ttl_seconds=60) is True
        await session.commit()
        assert await acquire_lease(session, "owner-a", ttl_seconds=60) is True


# --- due selection -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_selection_respects_tiers(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        fresh = await _stream(session, name="fresh")
        stale_cold = await _stream(session, name="stale-cold")
        stale_hot = await _stream(session, name="stale-hot")
        now = int(time.time())
        session.add_all(
            [
                StreamQuality(stream_id=fresh.id, last_checked_epoch=now),
                StreamQuality(stream_id=stale_cold.id, last_checked_epoch=now - 7200),
                StreamQuality(stream_id=stale_hot.id, last_checked_epoch=now - 400),
            ]
        )
        hot_channel = Channel(
            public_key="pk-hot",
            name="Hot",
            display_name="Hot",
            number=1,
            active_since_epoch=now - 60,
        )
        session.add(hot_channel)
        await session.flush()
        session.add(ChannelStream(channel_id=hot_channel.id, stream_id=stale_hot.id))
        cold_channel = Channel(
            public_key="pk-cold",
            name="Cold",
            display_name="Cold",
            number=2,
            active_since_epoch=now - 200_000,
        )
        session.add(cold_channel)
        await session.flush()
        session.add(ChannelStream(channel_id=cold_channel.id, stream_id=stale_cold.id))
        await session.commit()

    async with session_factory() as session:
        due = await due_stream_ids(session, batch_size=10, hot_seconds=300, cold_seconds=3600)
        assert stale_hot.id in due  # hot interval is 5 min, last check 400 s ago
        assert stale_cold.id in due  # cold interval is 1 h, last check 2 h ago
        assert fresh.id not in due


# --- tick --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_probes_due_streams(
    session_factory: async_sessionmaker[AsyncSession], origin: str
) -> None:
    async with session_factory() as session:
        await _stream(session, name="due", url=f"{origin}/v.m3u8")
        await session.commit()

    scheduler = ProbeScheduler(session_factory, tick_seconds=1, batch_size=5)
    stats = await scheduler.tick()
    assert stats["skipped"] is False
    assert stats["probed"] == 1
    assert stats["ok"] == 1

    async with session_factory() as session:
        quality = await session.scalar(select(StreamQuality))
        assert quality is not None
        assert quality.checks == 1
        assert quality.uptime_ratio == 1.0
        state = await session.get(SchedulerState, 1)
        assert state is not None
        assert state.rounds == 1
        assert state.probed_total == 1


@pytest.mark.asyncio
async def test_tick_is_skipped_while_another_owner_holds_the_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await acquire_lease(session, "someone-else", ttl_seconds=600)
        await session.commit()

    scheduler = ProbeScheduler(session_factory, tick_seconds=1)
    stats = await scheduler.tick()
    assert stats["skipped"] is True
    assert stats["probed"] == 0


@pytest.mark.asyncio
async def test_watchdog_flags_staleness(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        before = await scheduler_status(session, tick_seconds=60)
        assert before.healthy is False
        assert before.last_tick_epoch is None

    scheduler = ProbeScheduler(session_factory, tick_seconds=60)
    await scheduler.tick()

    async with session_factory() as session:
        after = await scheduler_status(session, tick_seconds=60)
        assert after.healthy is True
        assert after.rounds == 1
        assert after.seconds_since_tick is not None

        state = await session.get(SchedulerState, 1)
        assert state is not None
        state.last_tick_epoch = int(time.time()) - 10_000
        await session.commit()

    async with session_factory() as session:
        stale = await scheduler_status(session, tick_seconds=60)
        assert stale.healthy is False


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_api_requires_admin(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get("/api/v1/scheduler/status")).status_code == 401


@pytest.mark.asyncio
async def test_scheduler_api_reports_and_ticks(client: httpx.AsyncClient) -> None:
    status = await client.get("/api/v1/scheduler/status", headers=ADMIN_HEADERS)
    assert status.status_code == 200
    assert status.json()["healthy"] is False

    tick = await client.post("/api/v1/scheduler/tick", headers=ADMIN_HEADERS)
    assert tick.status_code == 200
    body = tick.json()
    assert body["skipped"] is False
    assert body["probed"] == 0  # no streams yet

    after = (await client.get("/api/v1/scheduler/status", headers=ADMIN_HEADERS)).json()
    assert after["healthy"] is True
    assert after["rounds"] >= 1


@pytest.mark.asyncio
async def test_stopping_the_scheduler_releases_the_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A redeploy must not wait for the lease to expire before probing resumes."""
    from iptvcc.services.scheduler import ProbeScheduler, acquire_lease, get_state

    async with session_factory() as session:
        first = ProbeScheduler(session_factory)
        assert await acquire_lease(session, first.owner, 180) is True
        await session.commit()

    # Stopping hands the lease back, so a replacement wins immediately.
    await first.stop()

    async with session_factory() as session:
        state = await get_state(session)
        assert state.owner is None
        second = ProbeScheduler(session_factory)
        assert await acquire_lease(session, second.owner, 180) is True
        await session.commit()
        await second.stop()


@pytest.mark.asyncio
async def test_due_sources_are_refreshed_without_a_click(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A playlist changes after import; the scheduler must pull it again when it is due."""
    from datetime import UTC, datetime, timedelta

    from iptvcc.config import get_settings
    from iptvcc.services import source_refresh

    playlist = '#EXTM3U\n#EXTINF:-1 tvg-id="x.test",Channel X\nhttp://p.test/x.ts\n'

    async def _fetch(_url: str) -> str:
        return playlist

    monkeypatch.setattr(source_refresh, "fetch_text", _fetch)
    monkeypatch.setattr(get_settings(), "source_refresh_hours", 6, raising=False)

    async with session_factory() as session:
        due = Source(
            name="due",
            url="http://p.test/a.m3u",
            last_import_at=datetime.now(UTC) - timedelta(hours=7),
        )
        fresh = Source(name="fresh", url="http://p.test/b.m3u", last_import_at=datetime.now(UTC))
        session.add_all([due, fresh])
        await session.commit()

        result = await source_refresh.refresh_due_sources(session, limit=5)
        assert result == {"refreshed": 1, "failed": 0}
        assert due.last_status == "success"
        assert fresh.last_status is None


@pytest.mark.asyncio
async def test_a_failing_refresh_is_recorded_and_not_retried_every_tick(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from iptvcc.config import get_settings
    from iptvcc.netguard import UnsafeUrlError
    from iptvcc.services import source_refresh

    async def _fetch(_url: str) -> str:
        raise UnsafeUrlError("provider returned HTTP 403")

    monkeypatch.setattr(source_refresh, "fetch_text", _fetch)
    monkeypatch.setattr(get_settings(), "source_refresh_hours", 6, raising=False)

    async with session_factory() as session:
        source = Source(name="broken", url="http://p.test/bad.m3u")
        session.add(source)
        await session.commit()

        result = await source_refresh.refresh_due_sources(session, limit=5)
        assert result == {"refreshed": 0, "failed": 1}
        assert source.last_status == "error"
        assert source.last_error == "provider returned HTTP 403"

        # The attempt timestamp moved, so the next tick does not hammer the same provider.
        again = await source_refresh.refresh_due_sources(session, limit=5)
        assert again == {"refreshed": 0, "failed": 0}
