"""Scale benchmark: import, playlist render, EPG ingest and guide build at a given size.

Usage (from the repo root, with the venv):

    .\\.venv\\Scripts\\python.exe scripts\\bench_scale.py --channels 10000 --sources 3
    .\\.venv\\Scripts\\python.exe scripts\\bench_scale.py --channels 2000 --epg-channels 500

It creates a throwaway SQLite database unless ``IPTV_DATABASE_URL`` is already set, so it never
touches real data. Every number printed is wall-clock time for the whole operation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("IPTV_SCHEDULER_ENABLED", "false")
os.environ.setdefault("IPTV_TELEGRAM_ENABLED", "false")
os.environ.setdefault("IPTV_APP_ENV", "test")

from iptvcc import db
from iptvcc.config import get_settings
from iptvcc.models import Assignment, Device, PlaylistProfile, Source
from iptvcc.security import hash_token
from iptvcc.services.delivery import build_playlist
from iptvcc.services.epg import build_guide, ingest_xmltv, map_channels
from iptvcc.services.importing import import_playlist


def build_m3u(channels: int, sources: int) -> str:
    """One EXTINF per stream, all sources sharing a tvg-id so matching collapses them."""
    lines = ["#EXTM3U"]
    for index in range(channels):
        name = f"Channel {index:05d} HD"
        for source in range(sources):
            label = name if source == 0 else f"{name} (backup {source})"
            lines.append(
                f'#EXTINF:-1 tvg-id="ch{index}.test" group-title="Group {index % 40:02d}",{label}'
            )
            lines.append(f"http://provider{source}.test/live/{index}.ts")
    return "\n".join(lines) + "\n"


def build_xmltv(channels: int, programmes: int, start: datetime) -> str:
    stamp = lambda moment: moment.strftime("%Y%m%d%H%M%S +0000")  # noqa: E731
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<tv>"]
    for index in range(channels):
        parts.append(
            f'<channel id="ch{index}.test">'
            f"<display-name>Channel {index:05d}</display-name></channel>"
        )
    for index in range(channels):
        for slot in range(programmes):
            begin = start + timedelta(minutes=30 * slot)
            parts.append(
                f'<programme start="{stamp(begin)}" stop="{stamp(begin + timedelta(minutes=30))}" '
                f'channel="ch{index}.test"><title>Programme {slot}</title>'
                f"<desc>Episode {slot} of channel {index}</desc></programme>"
            )
    parts.append("</tv>")
    return "\n".join(parts)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


async def run(channels: int, sources: int, epg_channels: int, programmes: int) -> int:
    settings = get_settings()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if not os.environ.get("IPTV_DATABASE_URL"):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db_path = Path(temporary.name) / "bench.db"
        object.__setattr__(settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
        print(f"database: {db_path}")
    else:
        print(f"database: {settings.database_url.split('@')[-1]}")

    await db.create_schema()
    results: list[tuple[str, float, str]] = []

    async with db.get_session_factory()() as session:
        m3u = build_m3u(channels, sources)
        print(
            f"m3u size: {len(m3u) / 1024 / 1024:.2f} MiB, {channels} channels x {sources} streams"
        )

        started = time.perf_counter()
        source = Source(name="Bench", url="http://bench.test/list.m3u")
        session.add(source)
        await session.flush()
        stats = await import_playlist(session, source, m3u)
        await session.commit()
        first_import = time.perf_counter() - started
        results.append(
            (
                "import (cold)",
                first_import,
                f"{stats.channels_created} channels, {stats.created} streams",
            )
        )

        started = time.perf_counter()
        stats_again = await import_playlist(session, source, m3u)
        await session.commit()
        results.append(
            ("import (re-run)", time.perf_counter() - started, f"{stats_again.unchanged} unchanged")
        )

        profile = PlaylistProfile(name="Bench", is_default=True)
        session.add(profile)
        device = Device(name="Bench TV")
        session.add(device)
        await session.flush()
        assignment = Assignment(
            device_id=device.id,
            playlist_profile_id=profile.id,
            token_hash=hash_token("bench-token"),
            token_prefix="bench",
        )
        session.add(assignment)
        await session.commit()

        timings: list[float] = []
        playlist_size = 0
        for _ in range(3):
            started = time.perf_counter()
            playlist = await build_playlist(session, "bench-token", "http://127.0.0.1:8000")
            timings.append(time.perf_counter() - started)
            playlist_size = len(playlist or "")
        if playlist_size == 0:
            raise SystemExit("playlist came back empty: the assignment token did not resolve")
        results.append(
            (
                "playlist render",
                statistics.median(timings),
                f"median of 3, {playlist_size / 1024:.0f} KiB, p90 {percentile(timings, 0.9):.2f}s",
            )
        )

        if epg_channels:
            total_programmes = epg_channels * programmes
            # `--programmes` is per channel: 500 x 20000 is ten million programmes and about a
            # gigabyte of XML, which used to be attempted and then died in the ingest limit.
            if total_programmes > 600_000:
                print(
                    f"{total_programmes:,} programmes requested "
                    f"({epg_channels} channels x {programmes}). That is far above any realistic "
                    "guide; lower --programmes (per channel) or --epg-channels."
                )
                return 2
            start = datetime.now(UTC).replace(second=0, microsecond=0)
            xml = build_xmltv(epg_channels, programmes, start)
            print(
                f"xmltv size: {len(xml) / 1024 / 1024:.2f} MiB, "
                f"{epg_channels * programmes} programmes"
            )

            started = time.perf_counter()
            from iptvcc.models import EpgSource

            epg_source = EpgSource(name="Bench guide", default_timezone="UTC")
            session.add(epg_source)
            await session.flush()
            ingested = await ingest_xmltv(session, epg_source, xml)
            await session.commit()
            results.append(
                ("epg ingest", time.perf_counter() - started, f"{ingested.programmes} programmes")
            )

            started = time.perf_counter()
            mapped = await map_channels(session)
            await session.commit()
            results.append(
                ("epg mapping", time.perf_counter() - started, f"{mapped} channels mapped")
            )

            started = time.perf_counter()
            written = await build_guide(
                session, "bench-token", Path(settings.artifacts_dir) / "bench"
            )
            elapsed = time.perf_counter() - started
            if written is None:
                results.append(("guide build", elapsed, "no guide produced"))
            else:
                xml_bytes = written[0].stat().st_size
                gz_bytes = written[1].stat().st_size
                results.append(
                    (
                        "guide build",
                        elapsed,
                        f"{xml_bytes / 1024 / 1024:.1f} MiB XML, "
                        f"{gz_bytes / 1024 / 1024:.1f} MiB gzip",
                    )
                )

    await db.dispose_engine()
    if temporary is not None:
        # Windows can keep the SQLite file locked for a moment; the OS cleans temp eventually.
        with contextlib.suppress(OSError):
            temporary.cleanup()

    print("\n| operation | time | notes |")
    print("|---|---|---|")
    for name, seconds, note in results:
        print(f"| {name} | {seconds:.2f} s | {note} |")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", type=int, default=1000)
    parser.add_argument("--sources", type=int, default=2)
    parser.add_argument("--epg-channels", type=int, default=0)
    parser.add_argument("--programmes", type=int, default=24)
    args = parser.parse_args()
    return asyncio.run(run(args.channels, args.sources, args.epg_channels, args.programmes))


if __name__ == "__main__":
    raise SystemExit(main())
