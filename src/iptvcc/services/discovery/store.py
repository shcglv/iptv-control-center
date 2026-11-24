"""Candidate store: deduplicate discovered links and keep them out of the catalog until reviewed."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import DiscoveryCandidate
from ...secrets import encrypt
from .base import DiscoveredItem


@dataclass
class StoreStats:
    created: int = 0
    updated: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"created": self.created, "updated": self.updated}


def dedupe_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


async def store_items(
    session: AsyncSession, *, adapter: str, items: list[DiscoveredItem]
) -> StoreStats:
    stats = StoreStats()
    now = datetime.now(UTC)
    hashes = [dedupe_hash(item.url) for item in items]
    seen_hashes: set[str] = set()
    existing = {
        row.dedupe_hash: row
        for row in (
            await session.scalars(
                select(DiscoveryCandidate).where(DiscoveryCandidate.dedupe_hash.in_(hashes))
            )
        ).all()
    }

    for item, digest in zip(items, hashes, strict=True):
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        row = existing.get(digest)
        if row is not None:
            row.last_seen_at = now
            row.adapter = adapter
            if row.title is None and item.title:
                row.title = item.title[:300]
            stats.updated += 1
            continue

        session.add(
            DiscoveryCandidate(
                adapter=adapter,
                kind=item.kind,
                url_enc=encrypt(item.url),
                url_masked=_mask(item.url),
                host=_host(item.url)[:255],
                title=(item.title or "")[:300] or None,
                author=(item.author or "")[:120] or None,
                published_at=(item.published_at or "")[:64] or None,
                snippet=(item.snippet or "")[:2000] or None,
                confidence=item.confidence,
                has_credentials=item.has_credentials,
                dedupe_hash=digest,
                status="new",
                provenance=item.provenance,
                last_seen_at=now,
            )
        )
        stats.created += 1
    await session.flush()
    return stats


def _mask(url: str) -> str:
    from ...secrets import mask_url

    return mask_url(url)[:300]
