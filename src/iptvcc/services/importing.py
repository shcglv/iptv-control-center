"""M3U import pipeline: parse -> upsert streams -> attach to channels.

Idempotent by design: re-importing the same playlist updates rows instead of duplicating them,
and streams that disappear are marked stale (never deleted), so a temporary provider outage
cannot erase a channel.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..m3u import Entry, iter_entries, validate_playlist
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

_BULK = 5000


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass
class ImportStats:
    parsed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    stale: int = 0
    skipped: int = 0
    channels_created: int = 0
    channels_linked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "parsed": self.parsed,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "stale": self.stale,
            "skipped": self.skipped,
            "channels_created": self.channels_created,
            "channels_linked": self.channels_linked,
        }


async def import_playlist(session: AsyncSession, source: Source, text: str) -> ImportStats:
    """Import a playlist body into ``source`` and return counters.

    New streams are inserted in bulk: a 30 000-stream playlist is a routine size, and inserting it
    through the ORM unit of work made the cold import an order of magnitude slower than the parse.
    """
    stats = ImportStats()
    now = datetime.now(UTC)
    lines = text.splitlines()
    validate_playlist(lines)
    entries = list(iter_entries(lines))
    stats.parsed = len(entries)

    existing_rows = (
        await session.scalars(select(Stream).where(Stream.source_id == source.id))
    ).all()
    existing = {row.stream_hash: row for row in existing_rows}

    touched: list[tuple[int, Entry]] = []
    seen_hashes: set[str] = set()
    pending: list[tuple[Entry, dict[str, object], str]] = []
    unchanged_hashes: list[str] = []
    # A URL may appear twice under different names or ids. The stream is one row, but both
    # entries must still resolve to a channel, so they are replayed once ids are known.
    duplicates: list[tuple[Entry, str]] = []

    for entry in entries:
        if not entry.url.lower().startswith(("http://", "https://")):
            stats.skipped += 1
            continue
        stream_hash = _digest(str(source.id), entry.url)
        stream_key = _digest(entry.url)[:32]
        row = existing.get(stream_hash)

        if row is None:
            if stream_hash in seen_hashes:
                duplicates.append((entry, stream_hash))
                continue
            seen_hashes.add(stream_hash)
            pending.append(
                (
                    entry,
                    {
                        "source_id": source.id,
                        "stream_key": stream_key,
                        "stream_hash": stream_hash,
                        "name": entry.name,
                        "tvg_id": entry.tvg_id,
                        "tvg_name": entry.tvg_name,
                        "logo_url": entry.logo,
                        "group_title": entry.group_title,
                        "url_enc": encrypt(entry.url),
                        "container": entry.container,
                        "is_stale": False,
                        "first_seen": now,
                        "last_seen": now,
                    },
                    stream_hash,
                )
            )
            continue

        changed = (
            row.name != entry.name
            or row.tvg_id != entry.tvg_id
            or row.logo_url != entry.logo
            or row.group_title != entry.group_title
            or row.container != entry.container
            or row.is_stale
        )
        if changed:
            row.name = entry.name
            row.tvg_id = entry.tvg_id
            row.tvg_name = entry.tvg_name
            row.logo_url = entry.logo
            row.group_title = entry.group_title
            row.container = entry.container
            row.is_stale = False
            row.last_seen = now
            stats.updated += 1
        else:
            # The heartbeat is applied as one bulk UPDATE below; marking thousands of rows dirty
            # here turned every no-op re-import into thousands of single-row updates.
            unchanged_hashes.append(stream_hash)
            stats.unchanged += 1

        seen_hashes.add(stream_hash)
        touched.append((row.id, entry))

    if unchanged_hashes:
        for start in range(0, len(unchanged_hashes), _BULK):
            await session.execute(
                update(Stream)
                .where(Stream.stream_hash.in_(unchanged_hashes[start : start + _BULK]))
                .values(last_seen=now)
                # Without this, SQLAlchemy evaluates the criteria against every Stream object in
                # the identity map (tens of thousands) and the update becomes quadratic.
                .execution_options(synchronize_session=False)
            )

    if pending:
        for start in range(0, len(pending), _BULK):
            batch = [row for _entry, row, _hash in pending[start : start + _BULK]]
            await session.execute(insert(Stream), batch)
        hashes = [stream_hash for _entry, _row, stream_hash in pending]
        ids: dict[str, int] = {}
        for start in range(0, len(hashes), _BULK):
            chunk = hashes[start : start + _BULK]
            rows = (
                await session.execute(
                    select(Stream.stream_hash, Stream.id).where(Stream.stream_hash.in_(chunk))
                )
            ).all()
            ids.update(dict(rows))
        for entry, _row, stream_hash in pending:
            stream_id = ids.get(stream_hash)
            if stream_id is None:
                continue
            touched.append((stream_id, entry))
            stats.created += 1
        for entry, stream_hash in duplicates:
            stream_id = ids.get(stream_hash)
            if stream_id is not None:
                touched.append((stream_id, entry))
        await session.flush()

    if touched:
        for row in existing.values():
            if row.stream_hash not in seen_hashes and not row.is_stale:
                row.is_stale = True
                stats.stale += 1

    await session.flush()
    await _attach_channels(session, touched, stats)

    source.stream_count = (
        await session.scalar(
            select(func.count())
            .select_from(Stream)
            .where(Stream.source_id == source.id, Stream.is_stale.is_(False))
        )
        or 0
    )
    source.last_import_at = now
    source.last_status = "success"
    source.last_error = None
    await session.flush()
    return stats


async def _attach_channels(
    session: AsyncSession, touched: list[tuple[int, Entry]], stats: ImportStats
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

    # One query per chunk for existing links, instead of one per stream.
    stream_ids = [stream_id for stream_id, _entry in touched]
    existing_links: set[tuple[str, int]] = set()
    for start in range(0, len(stream_ids), _BULK):
        existing_links.update(
            {
                (channel_id, stream_id)
                for channel_id, stream_id in (
                    await session.execute(
                        select(ChannelStream.channel_id, ChannelStream.stream_id).where(
                            ChannelStream.stream_id.in_(stream_ids[start : start + _BULK])
                        )
                    )
                ).all()
            }
        )

    # Pass 1: resolve or create channels and register them in the lookup maps immediately, so
    # later entries in the same import find them without a flush.
    resolved: list[tuple[int, Channel]] = []
    for stream_id, entry in touched:
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
            if tvg_id:
                by_tvg[tvg_id] = channel
            if match_key:
                by_key[match_key] = channel
            stats.channels_created += 1

        resolved.append((stream_id, channel))

    # A single flush assigns ids to every new channel.
    await session.flush()

    # Pass 2: build the missing links, then insert them in bulk.
    link_rows: list[dict[str, object]] = []
    for stream_id, channel in resolved:
        if (channel.id, stream_id) in existing_links:
            continue
        next_priority = link_counts.get(channel.id, 0)
        link_rows.append(
            {
                "channel_id": channel.id,
                "stream_id": stream_id,
                "priority": next_priority,
                "role": ROLE_PRIMARY,
            }
        )
        existing_links.add((channel.id, stream_id))
        link_counts[channel.id] = next_priority + 1
        stats.channels_linked += 1

    for start in range(0, len(link_rows), _BULK):
        await session.execute(insert(ChannelStream), link_rows[start : start + _BULK])


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
