"""Near-duplicate channel detection and merging.

Import matches exact normalised names only, which is the right default: silently merging "BBC One"
and "BBC One Scotland" would be a data-loss bug. The cost is that a provider spelling a name
slightly differently creates a duplicate channel. This module finds those pairs and lets a person
decide, one suggestion at a time.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..matching import FUZZY_ACCEPT, FUZZY_REVIEW, normalize, score
from ..models import (
    Channel,
    ChannelEpgMap,
    ChannelStream,
    MatchSuggestion,
    ProfileChannel,
)


@dataclass
class ScanStats:
    compared: int = 0
    created: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"compared": self.compared, "created": self.created, "skipped": self.skipped}


def _first_token(key: str) -> str:
    return key.split(" ", 1)[0] if key else ""


async def scan_for_duplicates(session: AsyncSession, *, max_pairs: int = 1000) -> ScanStats:
    """Compare channels that share a first token and record the plausible duplicates.

    Bucketing by the first word keeps this linear in practice: comparing every channel with every
    other would be 50 million comparisons on a 10 000-channel catalog.
    """
    stats = ScanStats()
    channels = (await session.scalars(select(Channel).where(Channel.is_hidden.is_(False)))).all()
    buckets: dict[str, list[Channel]] = {}
    for channel in channels:
        key = channel.match_key or normalize(channel.display_name or channel.name)
        if not key:
            continue
        buckets.setdefault(_first_token(key), []).append(channel)

    linked_counts = dict(
        (
            await session.execute(
                select(ChannelStream.channel_id, func.count()).group_by(ChannelStream.channel_id)
            )
        ).all()
    )
    existing = {
        (row.keep_id, row.drop_id) for row in (await session.scalars(select(MatchSuggestion))).all()
    }

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for index, left in enumerate(bucket):
            if stats.created >= max_pairs:
                return stats
            for right in bucket[index + 1 :]:
                stats.compared += 1
                left_key = left.match_key or normalize(left.display_name or left.name)
                right_key = right.match_key or normalize(right.display_name or right.name)
                if left_key == right_key:
                    continue  # same normalised name would have merged at import
                value = score(left_key, right_key)
                if value < FUZZY_REVIEW or value >= FUZZY_ACCEPT:
                    continue
                keep, drop = (
                    (left, right)
                    if linked_counts.get(left.id, 0) >= linked_counts.get(right.id, 0)
                    else (right, left)
                )
                if (keep.id, drop.id) in existing:
                    stats.skipped += 1
                    continue
                session.add(
                    MatchSuggestion(
                        keep_id=keep.id,
                        drop_id=drop.id,
                        score=round(value, 2),
                        status="new",
                    )
                )
                existing.add((keep.id, drop.id))
                stats.created += 1
                if stats.created >= max_pairs:
                    return stats

    await session.flush()
    return stats


async def merge_channels(session: AsyncSession, suggestion: MatchSuggestion) -> None:
    """Move everything from ``drop`` to ``keep``, then delete ``drop``.

    Links that already exist on the keep side are discarded rather than duplicated; a channel
    override on the keep side wins over the dropped one.
    """
    keep_id, drop_id = suggestion.keep_id, suggestion.drop_id
    # Read the decision from the database, not from the possibly-stale object: a second accept
    # must fail even when the row was removed by a cascade.
    current = await session.scalar(
        select(MatchSuggestion.status).where(MatchSuggestion.id == suggestion.id)
    )
    if current is None:
        raise ValueError("suggestion no longer exists")
    if current != "new":
        raise ValueError(f"suggestion already {current}")
    if keep_id == drop_id:
        raise ValueError("cannot merge a channel into itself")

    existing_streams = set(
        (
            await session.scalars(
                select(ChannelStream.stream_id).where(ChannelStream.channel_id == keep_id)
            )
        ).all()
    )
    for link in (
        await session.scalars(select(ChannelStream).where(ChannelStream.channel_id == drop_id))
    ).all():
        if link.stream_id in existing_streams:
            await session.delete(link)
        else:
            link.channel_id = keep_id
            existing_streams.add(link.stream_id)

    dropped_overrides = (
        await session.scalars(select(ProfileChannel).where(ProfileChannel.channel_id == drop_id))
    ).all()
    for override in dropped_overrides:
        existing = await session.get(ProfileChannel, (override.profile_id, keep_id))
        if existing is None:
            override.channel_id = keep_id
        else:
            await session.delete(override)

    for mapping in (
        await session.scalars(select(ChannelEpgMap).where(ChannelEpgMap.channel_id == drop_id))
    ).all():
        taken = await session.scalar(
            select(ChannelEpgMap.channel_id).where(ChannelEpgMap.channel_id == keep_id)
        )
        if taken is None:
            mapping.channel_id = keep_id
        else:
            await session.delete(mapping)

    # Any other suggestion that mentioned the dropped channel is now meaningless.
    await session.execute(
        delete(MatchSuggestion).where(
            MatchSuggestion.id != suggestion.id,
            (MatchSuggestion.keep_id == drop_id) | (MatchSuggestion.drop_id == drop_id),
        )
    )
    # Delete the resolved suggestion before the channel: the channel FK cascades, and relying on
    # that made the answer differ between SQLite (FKs off by default) and PostgreSQL.
    await session.execute(delete(MatchSuggestion).where(MatchSuggestion.id == suggestion.id))
    await session.execute(delete(Channel).where(Channel.id == drop_id))
    await session.flush()


async def reject_suggestion(session: AsyncSession, suggestion: MatchSuggestion) -> None:
    suggestion.status = "rejected"
    await session.flush()
