"""Health probes, aggregates, selection and the resolver's use of the ranking."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import (
    ROLE_PRIMARY,
    Channel,
    ChannelStream,
    HealthCheck,
    Source,
    Stream,
    StreamQuality,
)
from iptvcc.secrets import encrypt
from iptvcc.services.delivery import resolve_stream
from iptvcc.services.health import parse_master_playlist, probe_url, run_probe_round
from iptvcc.services.selector import rank_candidates, select_stream_id

MASTER = (
    b"#EXTM3U\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.42e01e,mp4a.40.2"\n'
    b"low.m3u8\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n'
    b"high.m3u8\n"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/broken"):
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(MASTER)))
        self.end_headers()
        self.wfile.write(MASTER)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def hls_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


# --- parsing and probing -----------------------------------------------------------------------


def test_parse_master_playlist_picks_highest_bandwidth() -> None:
    details = parse_master_playlist(MASTER)
    assert details["bandwidth_bps"] == 5_000_000
    assert details["resolution"] == "1920x1080"
    assert str(details["video_codec"]).startswith("avc1")
    assert str(details["audio_codec"]).startswith("mp4a")


def test_parse_media_playlist_has_no_variants() -> None:
    assert parse_master_playlist(b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n") == {}


@pytest.mark.asyncio
async def test_probe_url_reports_format_and_timing(hls_server: str) -> None:
    result = await probe_url(f"{hls_server}/master.m3u8")
    assert result.ok is True
    assert result.http_status == 200
    assert result.resolution == "1920x1080"
    assert result.bandwidth_bps == 5_000_000
    assert result.ttfb_ms is not None
    assert result.ttfb_ms >= 0
    assert result.dns_ms is not None


@pytest.mark.asyncio
async def test_probe_url_marks_http_errors(hls_server: str) -> None:
    result = await probe_url(f"{hls_server}/broken.m3u8")
    assert result.ok is False
    assert result.error_class == "http_503"


@pytest.mark.asyncio
async def test_probe_url_marks_connect_failures() -> None:
    result = await probe_url("http://127.0.0.1:9/nothing.ts", timeout=2.0)
    assert result.ok is False
    assert result.error_class in {"connect", "http", "timeout"}


# --- aggregates --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_round_writes_samples_and_aggregates(
    session_factory: async_sessionmaker[AsyncSession], hls_server: str
) -> None:
    async with session_factory() as session:
        source = Source(name="S", url="http://p.test/l.m3u")
        session.add(source)
        await session.flush()
        stream = Stream(
            source_id=source.id,
            stream_key="k1",
            stream_hash="h1",
            name="BBC",
            url_enc=encrypt(f"{hls_server}/master.m3u8"),
            container="m3u8",
        )
        session.add(stream)
        await session.flush()

        stats = await run_probe_round(session, limit=10)
        await session.commit()

        assert stats == {"probed": 1, "ok": 1, "failed": 0}
        sample = await session.scalar(select(HealthCheck))
        assert sample is not None
        assert sample.ok
        quality = await session.get(StreamQuality, stream.id)
        assert quality is not None
        assert quality.checks == 1
        assert quality.uptime_ratio == 1.0
        assert quality.resolution == "1920x1080"
        assert quality.last_ok_epoch is not None


# --- selection ---------------------------------------------------------------------------------


async def _seed_two_streams(
    session: AsyncSession, *, good_uptime: float, bad_uptime: float
) -> tuple[Channel, Stream, Stream]:
    source = Source(name="S", url="http://p.test/l.m3u")
    session.add(source)
    await session.flush()
    good = Stream(
        source_id=source.id,
        stream_key="good",
        stream_hash="hgood",
        name="Good",
        url_enc=encrypt("http://good.test/1.ts"),
        container="ts",
    )
    bad = Stream(
        source_id=source.id,
        stream_key="bad",
        stream_hash="hbad",
        name="Bad",
        url_enc=encrypt("http://bad.test/1.ts"),
        container="ts",
    )
    session.add_all([good, bad])
    await session.flush()

    channel = Channel(public_key="pk-test", name="BBC One", display_name="BBC One HD", number=1)
    session.add(channel)
    await session.flush()
    session.add_all(
        [
            ChannelStream(channel_id=channel.id, stream_id=good.id, priority=0, role=ROLE_PRIMARY),
            ChannelStream(channel_id=channel.id, stream_id=bad.id, priority=1, role="backup"),
        ]
    )
    now = int(time.time())
    session.add_all(
        [
            StreamQuality(
                stream_id=good.id,
                checks=20,
                failures=round(20 * (1 - good_uptime)),
                uptime_ratio=good_uptime,
                p90_ttfb_ms=300,
                resolution="1920x1080",
                video_codec="avc1",
                bandwidth_bps=5_000_000,
                last_checked_epoch=now,
            ),
            StreamQuality(
                stream_id=bad.id,
                checks=20,
                failures=round(20 * (1 - bad_uptime)),
                uptime_ratio=bad_uptime,
                p90_ttfb_ms=2500,
                resolution="1280x720",
                video_codec="hvc1",
                bandwidth_bps=1_000_000,
                last_checked_epoch=now,
            ),
        ]
    )
    await session.flush()
    return channel, good, bad


@pytest.mark.asyncio
async def test_ranking_prefers_reliable_stream(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        channel, good, _bad = await _seed_two_streams(session, good_uptime=1.0, bad_uptime=0.2)
        ranked = await rank_candidates(session, channel, "ts")
        assert next(candidate.stream_id for candidate in ranked) == good.id
        assert ranked[0].score > ranked[1].score


@pytest.mark.asyncio
async def test_selector_respects_dwell_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        channel, good, bad = await _seed_two_streams(session, good_uptime=1.0, bad_uptime=0.2)
        # Pretend the bad stream was chosen moments ago: within the dwell window the selector
        # must not flap, even though the other stream scores much higher.
        channel.active_stream_id = bad.id
        channel.active_since_epoch = int(time.time())
        await session.flush()
        assert await select_stream_id(session, channel, "ts") == bad.id

        # Once the dwell window has passed, the clearly better stream wins.
        channel.active_since_epoch = int(time.time()) - 3600
        await session.flush()
        assert await select_stream_id(session, channel, "ts") == good.id


@pytest.mark.asyncio
async def test_resolver_returns_best_stream(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        channel, _good, _bad = await _seed_two_streams(session, good_uptime=1.0, bad_uptime=0.1)
        resolved = await resolve_stream(session, channel.public_key, "ts")
        assert resolved is not None
        assert resolved.url == "http://good.test/1.ts"
        assert resolved.container == "ts"


@pytest.mark.asyncio
async def test_resolver_rejects_wrong_container(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        channel, _good, _bad = await _seed_two_streams(session, good_uptime=1.0, bad_uptime=0.1)
        assert await resolve_stream(session, channel.public_key, "m3u8") is None
