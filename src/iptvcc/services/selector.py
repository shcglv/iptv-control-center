"""Source selection: rank a channel's streams by quality and apply anti-flapping."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import ROLE_DISABLED, Channel, ChannelStream, StreamQuality
from ..quality import ScoreBreakdown, compute, should_switch


@dataclass
class Candidate:
    stream_id: int
    score: float
    breakdown: ScoreBreakdown
    container: str


def _rank(
    links: Iterable[ChannelStream],
    qualities: dict[int, StreamQuality],
    ext: str | None,
) -> list[Candidate]:
    """Rank links (pure: no database access).

    ``ext`` filters by container, because the resolver's URL suffix must match the stream it
    redirects to. ``None`` means any container — the playlist renderer uses that to decide which
    extension to advertise.
    """
    candidates: list[Candidate] = []
    for link in sorted(links, key=lambda item: (item.priority, item.id)):
        stream = link.stream
        if link.role == ROLE_DISABLED or stream.is_stale:
            continue
        if ext is not None and stream.container != ext:
            continue
        quality = qualities.get(stream.id)
        breakdown = compute(
            uptime_ratio=quality.uptime_ratio if quality else None,
            checks=quality.checks if quality else 0,
            failures=quality.failures if quality else 0,
            p90_ttfb_ms=quality.p90_ttfb_ms if quality else None,
            resolution=quality.resolution if quality else None,
            video_codec=quality.video_codec if quality else None,
            bandwidth_bps=quality.bandwidth_bps if quality else None,
            container=stream.container,
            requested_ext=ext if ext is not None else stream.container,
        )
        candidates.append(
            Candidate(
                stream_id=stream.id,
                score=breakdown.total,
                breakdown=breakdown,
                container=stream.container,
            )
        )
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.stream_id))
    return candidates


def rank_links(
    channel: Channel, qualities: dict[int, StreamQuality], ext: str | None
) -> list[Candidate]:
    """Rank a channel whose links are already eager-loaded (used by the playlist renderer)."""
    return _rank(channel.links, qualities, ext)


async def load_qualities(session: AsyncSession) -> dict[int, StreamQuality]:
    return {row.stream_id: row for row in (await session.scalars(select(StreamQuality))).all()}


async def rank_candidates(
    session: AsyncSession, channel: Channel, ext: str | None
) -> list[Candidate]:
    links = (
        await session.scalars(
            select(ChannelStream)
            .where(ChannelStream.channel_id == channel.id)
            .options(selectinload(ChannelStream.stream))
        )
    ).all()
    stream_ids = [link.stream_id for link in links]
    qualities = {
        row.stream_id: row
        for row in (
            await session.scalars(
                select(StreamQuality).where(StreamQuality.stream_id.in_(stream_ids))
            )
        ).all()
    }
    return _rank(links, qualities, ext)


async def select_stream_id(session: AsyncSession, channel: Channel, ext: str | None) -> int | None:
    """Pick a stream id, honouring the dwell time before switching away from the current one."""
    candidates = await rank_candidates(session, channel, ext)
    if not candidates:
        return None

    best = candidates[0]
    current_id = channel.active_stream_id
    if current_id is not None and len(candidates) > 1:
        current = next((item for item in candidates if item.stream_id == current_id), None)
        if current is not None:
            age = int(time.time()) - (channel.active_since_epoch or 0)
            if not should_switch(current.score, best.score, age_seconds=age):
                return current.stream_id

    if best.stream_id != current_id:
        channel.active_stream_id = best.stream_id
        channel.active_since_epoch = int(time.time())
    return best.stream_id
