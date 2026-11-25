"""Channel identity catalog and take-down blocks.

The catalog gives channels a stable identity that does not depend on a provider's spelling. Matching
is deliberately exact on the normalised name (plus aliases), with a country tie-break: fuzzy
matching over tens of thousands of entries is both slow and capable of silently merging two real
channels, which is worse than leaving a channel unmatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..matching import normalize, region_of
from ..models import CatalogBlock, CatalogChannel, Channel, Source

BLOCK_KINDS = {"catalog_id", "tvg_id", "name", "domain"}

# tvg-id suffixes and iptv-org country codes disagree on a few well-known cases.
_COUNTRY_ALIASES = {"uk": "gb"}


def _same_country(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    a = left.lower()
    b = right.lower()
    return _COUNTRY_ALIASES.get(a, a) == _COUNTRY_ALIASES.get(b, b)


@dataclass
class CatalogStats:
    received: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


@dataclass
class MatchStats:
    matched: int = 0
    unmatched: int = 0
    already: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"matched": self.matched, "unmatched": self.unmatched, "already": self.already}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return []


async def ingest_catalog(session: AsyncSession, rows: list[dict[str, Any]]) -> CatalogStats:
    """Upsert catalog rows. The payload is iptv-org's channel object shape."""
    stats = CatalogStats(received=len(rows))
    existing = {
        row.catalog_id: row for row in (await session.scalars(select(CatalogChannel))).all()
    }
    now = datetime.now(UTC)

    for raw in rows:
        catalog_id = str(raw.get("id") or raw.get("catalog_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not catalog_id or not name:
            stats.skipped += 1
            continue

        alt_names = _as_list(raw.get("alt_names"))
        categories = _as_list(raw.get("categories"))
        country = (raw.get("country") or None) if isinstance(raw.get("country"), str) else None
        logo = raw.get("logo")
        website = raw.get("website")
        is_nsfw = bool(raw.get("is_nsfw", False))

        row = existing.get(catalog_id)
        if row is None:
            row = CatalogChannel(
                catalog_id=catalog_id,
                name=name,
                alt_names=alt_names,
                country=country,
                categories=categories,
                logo_url=str(logo) if logo else None,
                website=str(website) if website else None,
                is_nsfw=is_nsfw,
                updated_at=now,
            )
            session.add(row)
            existing[catalog_id] = row
            stats.created += 1
            continue

        row.name = name
        row.alt_names = alt_names
        row.country = country
        row.categories = categories
        row.logo_url = str(logo) if logo else None
        row.website = str(website) if website else None
        row.is_nsfw = is_nsfw
        row.updated_at = now
        stats.updated += 1

    await session.flush()
    return stats


async def match_channels(session: AsyncSession) -> MatchStats:
    """Attach catalog identity to channels that do not have it yet."""
    stats = MatchStats()
    catalog = (await session.scalars(select(CatalogChannel))).all()
    if not catalog:
        return stats

    index: dict[str, list[CatalogChannel]] = {}
    for entry in catalog:
        for label in [entry.name, *entry.alt_names]:
            key = normalize(label)
            if not key:
                continue
            bucket = index.setdefault(key, [])
            if entry not in bucket:
                bucket.append(entry)

    channels = (await session.scalars(select(Channel))).all()
    for channel in channels:
        if channel.catalog_id:
            stats.already += 1
            continue

        key = channel.match_key or normalize(channel.display_name or channel.name)
        candidates = index.get(key) if key else None
        if not candidates:
            stats.unmatched += 1
            continue

        region = region_of(channel.tvg_id, channel.display_name)
        chosen = candidates[0]
        if region is not None:
            for candidate in candidates:
                if _same_country(candidate.country, region):
                    chosen = candidate
                    break

        channel.catalog_id = chosen.catalog_id
        if not channel.tvg_id:
            channel.tvg_id = chosen.catalog_id
            channel.epg_id = chosen.catalog_id
        stats.matched += 1

    await session.flush()
    return stats


async def apply_blocklist(session: AsyncSession) -> dict[str, int]:
    """Enforce every block: channels are hidden, sources are disabled."""
    blocks = (await session.scalars(select(CatalogBlock))).all()
    hidden = 0
    disabled = 0

    for block in blocks:
        value = block.value.strip()
        if not value:
            continue

        if block.kind == "domain":
            host = value.lower()
            for source in (await session.scalars(select(Source))).all():
                if (urlsplit(source.url).hostname or "").lower() == host and source.enabled:
                    source.enabled = False
                    disabled += 1
            continue

        statement = select(Channel)
        if block.kind == "catalog_id":
            statement = statement.where(Channel.catalog_id == value)
        elif block.kind == "tvg_id":
            statement = statement.where(Channel.tvg_id == value)
        elif block.kind == "name":
            statement = statement.where(Channel.match_key == normalize(value))
        else:
            continue

        for channel in (await session.scalars(statement)).all():
            if not channel.is_hidden:
                channel.is_hidden = True
                hidden += 1

    await session.flush()
    return {"channels_hidden": hidden, "sources_disabled": disabled}


async def blocklist_summary(session: AsyncSession) -> dict[str, int]:
    blocks = (await session.scalars(select(CatalogBlock))).all()
    summary: dict[str, int] = {}
    for block in blocks:
        summary[block.kind] = summary.get(block.kind, 0) + 1
    return summary


async def catalog_status(session: AsyncSession) -> dict[str, int]:
    from sqlalchemy import func

    total = await session.scalar(select(func.count()).select_from(CatalogChannel)) or 0
    matched = (
        await session.scalar(
            select(func.count()).select_from(Channel).where(Channel.catalog_id.is_not(None))
        )
        or 0
    )
    channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
    blocks = await session.scalar(select(func.count()).select_from(CatalogBlock)) or 0
    return {
        "catalog_channels": total,
        "channels": channels,
        "matched_channels": matched,
        "unmatched_channels": max(0, channels - matched),
        "blocks": blocks,
    }


__all__ = [
    "BLOCK_KINDS",
    "CatalogStats",
    "MatchStats",
    "apply_blocklist",
    "blocklist_summary",
    "catalog_status",
    "ingest_catalog",
    "match_channels",
]
