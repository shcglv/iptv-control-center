"""Stream health probes.

The light probe answers three questions cheaply: does the host resolve, does the URL answer, and
what does the playlist say about the stream's format. It reads at most 2 KB and closes, so it costs
one short connection per check — never a sustained one, and never a media download.

Timings: ``dns_ms`` (separate lookup) and ``ttfb_ms`` (request start to first body byte, which
includes connect and TLS). Connect time is not measured separately to avoid a second connection.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse, urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import HealthCheck, Stream, StreamQuality
from ..secrets import decrypt

PROBE_TIMEOUT = 8.0
MAX_BYTES = 2048
MAX_CONCURRENCY = 2
PRUNE_SECONDS = 7 * 86_400
USER_AGENT = "iptv-control-center/0.1 (+probe)"

_BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)")
_RESOLUTION_RE = re.compile(r"RESOLUTION=(\d+x\d+)", re.IGNORECASE)
_CODECS_RE = re.compile(r'CODECS="([^"]+)"')


@dataclass
class ProbeResult:
    ok: bool
    dns_ms: float | None = None
    ttfb_ms: float | None = None
    http_status: int | None = None
    error_class: str | None = None
    resolution: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bandwidth_bps: int | None = None


def parse_master_playlist(body: bytes) -> dict[str, object]:
    """Extract the richest variant's format from an HLS master playlist."""
    text = body.decode("utf-8", errors="replace")
    if "#EXT-X-STREAM-INF" not in text:
        return {}
    best: dict[str, object] = {}
    best_bandwidth = -1
    for line in text.splitlines():
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        bandwidth_match = _BANDWIDTH_RE.search(line)
        bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
        if bandwidth <= best_bandwidth:
            continue
        best_bandwidth = bandwidth
        resolution_match = _RESOLUTION_RE.search(line)
        codecs_match = _CODECS_RE.search(line)
        video = audio = None
        if codecs_match:
            parts = [part.strip() for part in codecs_match.group(1).split(",")]
            video = next((part for part in parts if part and not part.startswith("mp4a")), None)
            audio = next((part for part in parts if part.startswith("mp4a")), None)
        best = {
            "bandwidth_bps": bandwidth or None,
            "resolution": resolution_match.group(1) if resolution_match else None,
            "video_codec": video,
            "audio_codec": audio,
        }
    return best


async def _dns_ms(host: str, port: int | None) -> float | None:
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    try:
        await asyncio.wait_for(
            loop.getaddrinfo(host, port or 80, type=socket.SOCK_STREAM), timeout=PROBE_TIMEOUT
        )
    except (OSError, TimeoutError):
        return None
    return round((time.perf_counter() - started) * 1000, 1)


async def probe_url(url: str, *, timeout: float = PROBE_TIMEOUT) -> ProbeResult:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ProbeResult(ok=False, error_class="unsupported_scheme")

    dns = await _dns_ms(parts.hostname, parts.port)
    if dns is None:
        return ProbeResult(ok=False, error_class="dns")

    started = time.perf_counter()
    try:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
                trust_env=False,
                headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{MAX_BYTES - 1}"},
            ) as client,
            client.stream("GET", url) as response,
        ):
            first_byte_at: float | None = None
            body = b""
            async for chunk in response.aiter_bytes():
                if first_byte_at is None:
                    first_byte_at = time.perf_counter()
                body += chunk
                if len(body) >= MAX_BYTES:
                    break
            ttfb = round(((first_byte_at or time.perf_counter()) - started) * 1000, 1)
            status = response.status_code
    except httpx.TimeoutException:
        return ProbeResult(ok=False, dns_ms=dns, error_class="timeout")
    except httpx.HTTPError:
        return ProbeResult(ok=False, dns_ms=dns, error_class="http")
    except OSError:
        return ProbeResult(ok=False, dns_ms=dns, error_class="connect")

    if status >= 400:
        return ProbeResult(
            ok=False, dns_ms=dns, ttfb_ms=ttfb, http_status=status, error_class=f"http_{status}"
        )

    details = parse_master_playlist(body) if body.startswith(b"#EXTM3U") else {}
    return ProbeResult(
        ok=True,
        dns_ms=dns,
        ttfb_ms=ttfb,
        http_status=status,
        resolution=details.get("resolution"),  # type: ignore[arg-type]
        video_codec=details.get("video_codec"),  # type: ignore[arg-type]
        audio_codec=details.get("audio_codec"),  # type: ignore[arg-type]
        bandwidth_bps=details.get("bandwidth_bps"),  # type: ignore[arg-type]
    )


@dataclass
class MediaInfo:
    """What an active inspection learned. ``error`` is set when the inspection itself failed."""

    resolution: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bandwidth_bps: int | None = None
    stream_count: int = 0
    error: str | None = None


def ffprobe_command(binary: str, url: str, timeout: float) -> list[str]:
    """Arguments for an inspection that reads a few seconds of the stream, not the whole VOD."""
    return [
        binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-rw_timeout",
        str(int(timeout * 1_000_000)),
        url,
    ]


def parse_ffprobe_json(payload: str) -> MediaInfo:
    try:
        data = json.loads(payload)
    except ValueError:
        return MediaInfo(error="unreadable ffprobe output")
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    resolution = None
    if video and video.get("width") and video.get("height"):
        resolution = f"{video['width']}x{video['height']}"
    bit_rate = (data.get("format") or {}).get("bit_rate") or (video or {}).get("bit_rate")
    try:
        bandwidth = int(bit_rate) if bit_rate else None
    except (TypeError, ValueError):
        bandwidth = None
    return MediaInfo(
        resolution=resolution,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        bandwidth_bps=bandwidth,
        stream_count=len(streams),
    )


async def probe_media(url: str, *, binary: str = "ffprobe", timeout: float = 12.0) -> MediaInfo:
    """Inspect a stream with ffprobe. Any failure is reported, never raised.

    Runs in a worker thread rather than through ``asyncio.create_subprocess_exec``: the selector
    event loop (required by psycopg on Windows) does not implement subprocess transports, and the
    inspection is a short blocking call that concurrency already bounds.
    """
    command = ffprobe_command(binary, url, timeout)
    try:
        completed = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, timeout=timeout + 2
        )
    except FileNotFoundError:
        return MediaInfo(error="ffprobe is not installed")
    except subprocess.TimeoutExpired:
        return MediaInfo(error="ffprobe timed out")
    except OSError as exc:
        return MediaInfo(error=f"ffprobe could not start: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        return MediaInfo(
            error=(detail[-1] if detail else f"ffprobe exit {completed.returncode}")[:200]
        )
    return parse_ffprobe_json(completed.stdout.decode("utf-8", errors="replace"))


async def probe_stream(stream: Stream, *, timeout: float = PROBE_TIMEOUT) -> ProbeResult:
    try:
        url = decrypt(stream.url_enc)
    except Exception:
        return ProbeResult(ok=False, error_class="decrypt")
    result = await probe_url(url, timeout=timeout)
    if result.ok and result.resolution is None and stream.container == "m3u8":
        # A media playlist with no variants: the stream is alive, format unknown.
        result.resolution = None

    settings = get_settings()
    if result.ok and settings.probe_mode == "active":
        info = await probe_media(
            url, binary=settings.ffprobe_path, timeout=settings.ffprobe_timeout_seconds
        )
        if info.error is not None:
            # Active mode means "can it actually be decoded", so a failed inspection is a failure.
            result.ok = False
            result.error_class = "ffprobe"
        elif info.stream_count == 0:
            # ffprobe can exit 0 on a manifest whose variants are gone: no streams is not healthy.
            result.ok = False
            result.error_class = "ffprobe_no_streams"
        else:
            result.resolution = info.resolution or result.resolution
            result.video_codec = info.video_codec or result.video_codec
            result.audio_codec = info.audio_codec or result.audio_codec
            result.bandwidth_bps = info.bandwidth_bps or result.bandwidth_bps
    return result


async def run_probe_round(
    session: AsyncSession, *, limit: int = 50, stream_ids: Sequence[int] | None = None
) -> dict[str, int]:
    """Probe live streams and refresh their aggregates.

    With ``stream_ids`` the round probes exactly those streams (used by the scheduler, which has
    already applied the tier intervals). Without it, the least recently checked streams are taken.
    """
    statement = (
        select(Stream, StreamQuality)
        .outerjoin(StreamQuality, StreamQuality.stream_id == Stream.id)
        .where(Stream.is_stale.is_(False))
    )
    if stream_ids is not None:
        if not stream_ids:
            return {"probed": 0, "ok": 0, "failed": 0}
        statement = statement.where(Stream.id.in_(list(stream_ids)))
    else:
        statement = statement.order_by(
            StreamQuality.last_checked_epoch.is_(None).desc(),
            StreamQuality.last_checked_epoch,
            Stream.id,
        ).limit(limit)

    rows = (await session.execute(statement)).all()

    if not rows:
        return {"probed": 0, "ok": 0, "failed": 0}

    settings = get_settings()
    semaphore = asyncio.Semaphore(max(1, settings.probe_concurrency))
    # A provider sees one host. The global semaphore caps total work, but without a per-host cap a
    # single source could still receive every concurrent probe in the batch and throttle us.
    host_semaphores: dict[str, asyncio.Semaphore] = {}
    per_host = max(1, settings.probe_host_concurrency)

    def host_of(url: str) -> str:
        return urlparse(url).hostname or "unknown"

    def host_sem(host: str) -> asyncio.Semaphore:
        if host not in host_semaphores:
            host_semaphores[host] = asyncio.Semaphore(per_host)
        return host_semaphores[host]

    async def run(stream: Stream) -> tuple[Stream, ProbeResult]:
        host = host_sem(host_of(decrypt(stream.url_enc)))
        async with semaphore, host:
            return stream, await probe_stream(stream)

    results = await asyncio.gather(*(run(stream) for stream, _quality in rows))

    now = int(time.time())
    ok_count = 0
    for stream, result in results:
        if result.ok:
            ok_count += 1
        session.add(
            HealthCheck(
                stream_id=stream.id,
                checked_epoch=now,
                ok=result.ok,
                dns_ms=result.dns_ms,
                ttfb_ms=result.ttfb_ms,
                http_status=result.http_status,
                error_class=result.error_class,
            )
        )
        quality = await session.get(StreamQuality, stream.id)
        if quality is None:
            quality = StreamQuality(stream_id=stream.id)
            session.add(quality)
        _apply_result(quality, result, now)
        await session.flush()
        quality.p90_ttfb_ms = await _p90_ttfb(session, stream.id)

    await session.execute(
        delete(HealthCheck).where(HealthCheck.checked_epoch < now - PRUNE_SECONDS)
    )
    await session.flush()
    return {"probed": len(results), "ok": ok_count, "failed": len(results) - ok_count}


def _apply_result(quality: StreamQuality, result: ProbeResult, now: int) -> None:
    # Columns may still be unset on a freshly constructed row: normalise before arithmetic.
    quality.checks = (quality.checks or 0) + 1
    quality.failures = quality.failures or 0
    quality.consecutive_failures = quality.consecutive_failures or 0
    quality.last_checked_epoch = now
    if result.ok:
        quality.last_ok_epoch = now
        quality.consecutive_failures = 0
        quality.last_error_class = None
        if result.ttfb_ms is not None:
            previous = quality.avg_ttfb_ms or 0.0
            previous_checks = max(1, quality.checks - 1)
            quality.avg_ttfb_ms = round(
                (previous * previous_checks + result.ttfb_ms) / quality.checks, 1
            )
        if result.resolution:
            quality.resolution = result.resolution
        if result.video_codec:
            quality.video_codec = result.video_codec
        if result.audio_codec:
            quality.audio_codec = result.audio_codec
        if result.bandwidth_bps:
            quality.bandwidth_bps = result.bandwidth_bps
    else:
        quality.failures += 1
        quality.consecutive_failures += 1
        quality.last_error_class = result.error_class
    quality.uptime_ratio = round(
        (quality.checks - quality.failures) / quality.checks if quality.checks else 0.0, 4
    )


async def _p90_ttfb(session: AsyncSession, stream_id: int) -> float | None:
    values = (
        await session.scalars(
            select(HealthCheck.ttfb_ms)
            .where(HealthCheck.stream_id == stream_id, HealthCheck.ok.is_(True))
            .order_by(HealthCheck.checked_epoch.desc())
            .limit(50)
        )
    ).all()
    samples = sorted(value for value in values if value is not None)
    if not samples:
        return None
    index = min(len(samples) - 1, round(0.9 * (len(samples) - 1)))
    return round(samples[index], 1)
