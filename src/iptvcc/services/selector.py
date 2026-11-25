"""Source selection: rank a channel's streams by quality and apply anti-flapping.

The playlist renderer must rank every link of every channel on each request, so the hot path does
not go through ORM entities: links are read as flat tuples into :class:`LinkView`. Ranking itself is
pure, which keeps it testable without a database.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import ROLE_DISABLED, Channel, ChannelStream, Stream, StreamQuality
from ..quality import ScoreBreakdown, compute, should_switch

_CHUNK = 5000


@dataclass(frozen=True)
class LinkView:
    id: int
    stream_id: int
    priority: int
    role: str
    container: str
    is_stale: bool


@dataclass
class Candidate:
    stream_id: int
    score: float
    breakdown: ScoreBreakdown
    container: str


def view_of(link: ChannelStream) -> LinkView:
    """Build a view from an ORM link whose ``stream`` is already loaded."""
    return LinkView(
        id=link.id,
        stream_id=link.stream_id,
        priority=link.priority,
        role=link.role,
        container=link.stream.container,
        is_stale=link.stream.is_stale,
    )


def _rank(
    links: Iterable[LinkView],
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
        if link.role == ROLE_DISABLED or link.is_stale:
            continue
        if ext is not None and link.container != ext:
            continue
        quality = qualities.get(link.stream_id)
        breakdown = compute(
            uptime_ratio=quality.uptime_ratio if quality else None,
            checks=quality.checks if quality else 0,
            failures=quality.failures if quality else 0,
            p90_ttfb_ms=quality.p90_ttfb_ms if quality else None,
            resolution=quality.resolution if quality else None,
            video_codec=quality.video_codec if quality else None,
            bandwidth_bps=quality.bandwidth_bps if quality else None,
            container=link.container,
            requested_ext=ext if ext is not None else link.container,
        )
        candidates.append(
            Candidate(
                stream_id=link.stream_id,
                score=breakdown.total,
                breakdown=breakdown,
                container=link.container,
            )
        )
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.stream_id))
    return candidates


def rank_links(
    channel: Channel, qualities: dict[int, StreamQuality], ext: str | None
) -> list[Candidate]:
    """Rank a channel whose links are already eager-loaded (single-channel paths)."""
    return _rank([view_of(link) for link in channel.links], qualities, ext)


def rank_views(
    links: Sequence[LinkView], qualities: dict[int, StreamQuality], ext: str | None
) -> list[Candidate]:
    """Rank pre-loaded link views (playlist renderer)."""
    return _rank(links, qualities, ext)


async def load_qualities(session: AsyncSession) -> dict[int, StreamQuality]:
    return {row.stream_id: row for row in (await session.scalars(select(StreamQuality))).all()}


async def load_link_views(
    session: AsyncSession, channel_ids: Sequence[str]
) -> dict[str, list[LinkView]]:
    """One flat query for every link of the given channels, with the few fields ranking needs."""
    result: dict[str, list[LinkView]] = defaultdict(list)
    ids = list(channel_ids)
    for start in range(0, len(ids), _CHUNK):
        rows = (
            await session.execute(
                select(
                    ChannelStream.channel_id,
                    ChannelStream.id,
                    ChannelStream.stream_id,
                    ChannelStream.priority,
                    ChannelStream.role,
                    Stream.container,
                    Stream.is_stale,
                )
                .join(Stream, Stream.id == ChannelStream.stream_id)
                .where(ChannelStream.channel_id.in_(ids[start : start + _CHUNK]))
            )
        ).all()
        for channel_id, link_id, stream_id, priority, role, container, is_stale in rows:
            result[channel_id].append(
                LinkView(
                    id=link_id,
                    stream_id=stream_id,
                    priority=priority,
                    role=role,
                    container=container,
                    is_stale=bool(is_stale),
                )
            )
    return dict(result)


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
    return _rank([view_of(link) for link in links], qualities, ext)


async def select_stream_id(
    session: AsyncSession,
    channel: Channel,
    ext: str | None,
    *,
    pinned_stream_id: int | None = None,
) -> int | None:
    """Pick a stream id.

    A profile pin wins when that stream is playable in the requested container; the dwell-time
    rule then protects the current choice from flapping.
    """
    candidates = await rank_candidates(session, channel, ext)
    if not candidates:
        return None

    if pinned_stream_id is not None:
        pinned = next((item for item in candidates if item.stream_id == pinned_stream_id), None)
        if pinned is not None:
            return pinned.stream_id

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
