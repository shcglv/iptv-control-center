"""M3U import pipeline: parse -> upsert streams -> attach to channels.

Idempotent by design: re-importing the same playlist updates rows instead of duplicating them,
and streams that disappear are marked stale (never deleted), so a temporary provider outage
cannot erase a channel.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..m3u import Entry, iter_entries
from ..matching import normalize
from ..models import (
    ROLE_PRIMARY,
    Category,
    Channel,
    ChannelStream,
    Source,
    Stream,
)
from ..secrets import encrypt
from ..security import new_public_key


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass
class ImportStats:
    parsed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    stale: int = 0
    channels_created: int = 0
    channels_linked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "parsed": self.parsed,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "stale": self.stale,
            "channels_created": self.channels_created,
            "channels_linked": self.channels_linked,
        }


async def import_playlist(session: AsyncSession, source: Source, text: str) -> ImportStats:
    """Import a playlist body into ``source`` and return counters."""
    stats = ImportStats()
    now = datetime.now(UTC)
    entries = list(iter_entries(text.splitlines()))
    stats.parsed = len(entries)

    existing_rows = (
        await session.scalars(select(Stream).where(Stream.source_id == source.id))
    ).all()
    existing = {row.stream_hash: row for row in existing_rows}

    touched: list[tuple[Stream, Entry]] = []
    seen: set[int] = set()

    for entry in entries:
        stream_hash = _digest(str(source.id), entry.url)
        stream_key = _digest(entry.url)[:32]
        row = existing.get(stream_hash)

        if row is None:
            row = Stream(
                source_id=source.id,
                stream_key=stream_key,
                stream_hash=stream_hash,
                name=entry.name,
                tvg_id=entry.tvg_id,
                tvg_name=entry.tvg_name,
                logo_url=entry.logo,
                group_title=entry.group_title,
                url_enc=encrypt(entry.url),
                container=entry.container,
                first_seen=now,
                last_seen=now,
            )
            session.add(row)
            existing[stream_hash] = row
            stats.created += 1
        else:
            changed = (
                row.name != entry.name
                or row.tvg_id != entry.tvg_id
                or row.logo_url != entry.logo
                or row.group_title != entry.group_title
                or row.container != entry.container
                or row.is_stale
            )
            row.name = entry.name
            row.tvg_id = entry.tvg_id
            row.tvg_name = entry.tvg_name
            row.logo_url = entry.logo
            row.group_title = entry.group_title
            row.container = entry.container
            row.is_stale = False
            row.last_seen = now
            if changed:
                stats.updated += 1
            else:
                stats.unchanged += 1

        seen.add(id(row))
        touched.append((row, entry))

    if touched:
        for row in existing.values():
            if id(row) not in seen and not row.is_stale:
                row.is_stale = True
                stats.stale += 1

    await session.flush()
    await _attach_channels(session, touched, stats)

    source.stream_count = sum(1 for row in existing.values() if not row.is_stale)
    source.last_import_at = now
    source.last_status = "success"
    source.last_error = None
    await session.flush()
    return stats


async def _attach_channels(
    session: AsyncSession, touched: list[tuple[Stream, Entry]], stats: ImportStats
) -> None:
    channels = (await session.scalars(select(Channel))).all()
    by_tvg = {channel.tvg_id: channel for channel in channels if channel.tvg_id}
    by_key = {channel.match_key: channel for channel in channels if channel.match_key}
    max_number = max((channel.number or 0 for channel in channels), default=0)

    categories = {row.name: row for row in (await session.scalars(select(Category))).all()}
    link_counts = dict(
        (
            await session.execute(
                select(ChannelStream.channel_id, func.count()).group_by(ChannelStream.channel_id)
            )
        ).all()
    )

    for stream, entry in touched:
        tvg_id = entry.tvg_id
        match_key = normalize(entry.name) or None

        channel = by_tvg.get(tvg_id) if tvg_id else None
        if channel is None and match_key is not None:
            channel = by_key.get(match_key)

        if channel is None:
            max_number += 1
            category_id = await _category_id(session, categories, entry.group_title)
            display = entry.name or entry.tvg_name or "Unnamed"
            channel = Channel(
                public_key=new_public_key(),
                name=display,
                display_name=display,
                number=max_number,
                category_id=category_id,
                logo_url=entry.logo,
                tvg_id=tvg_id,
                epg_id=tvg_id,
                match_key=match_key,
            )
            session.add(channel)
            await session.flush()
            if tvg_id:
                by_tvg[tvg_id] = channel
            if match_key:
                by_key[match_key] = channel
            stats.channels_created += 1

        next_priority = link_counts.get(channel.id, 0)
        already_linked = await session.scalar(
            select(ChannelStream.id).where(
                ChannelStream.channel_id == channel.id,
                ChannelStream.stream_id == stream.id,
            )
        )
        if already_linked is None:
            session.add(
                ChannelStream(
                    channel_id=channel.id,
                    stream_id=stream.id,
                    priority=next_priority,
                    role=ROLE_PRIMARY,
                )
            )
            link_counts[channel.id] = next_priority + 1
            stats.channels_linked += 1


async def _category_id(
    session: AsyncSession, categories: dict[str, Category], name: str | None
) -> int | None:
    if not name:
        return None
    category = categories.get(name)
    if category is None:
        category = Category(name=name, sort_index=len(categories))
        session.add(category)
        await session.flush()
        categories[name] = category
    return category.id
