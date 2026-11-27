"""Refresh M3U sources on a schedule, so a provider playlist updates without a click.

Playlists change: channels move, streams appear and disappear, a subscription is renewed. Until now
a source was imported only when an operator pressed the button, so the catalog drifted from the
provider the moment the import finished. The scheduler calls :func:`refresh_due_sources` each tick.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..m3u import PlaylistError
from ..models import Source
from ..netguard import UnsafeUrlError, fetch_text
from .importing import import_playlist

logger = logging.getLogger(__name__)


def _is_playlist(body: str) -> bool:
    return body.lstrip("\ufeff").lstrip().upper().startswith("#EXTM3U")


async def refresh_source(session: AsyncSession, source: Source) -> bool:
    """Re-import one source in place. Returns True on success and records failures on the row."""
    if not source.url:
        source.last_status = "error"
        source.last_error = "source has no URL"
        source.last_import_at = datetime.now(UTC)
        return False

    try:
        body = await fetch_text(source.url)
    except UnsafeUrlError as exc:
        source.last_status = "error"
        source.last_error = str(exc)
        source.last_import_at = datetime.now(UTC)
        return False

    if not _is_playlist(body):
        source.last_status = "error"
        source.last_error = "not an M3U playlist"
        source.last_import_at = datetime.now(UTC)
        return False

    try:
        stats = await import_playlist(session, source, body)
    except PlaylistError as exc:
        source.last_status = "error"
        source.last_error = str(exc)
        source.last_import_at = datetime.now(UTC)
        return False

    logger.info(
        "refresh source=%s parsed=%s created=%s unchanged=%s",
        source.id,
        stats.parsed,
        stats.created,
        stats.unchanged,
    )
    return True


async def refresh_due_sources(session: AsyncSession, *, limit: int = 3) -> dict[str, int]:
    """Refresh up to ``limit`` sources whose last attempt is older than the configured interval."""
    hours = get_settings().source_refresh_hours
    if hours <= 0:
        return {"refreshed": 0, "failed": 0}

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = await session.execute(
        select(Source)
        .where(
            Source.enabled.is_(True),
            Source.url.is_not(None),
            Source.url != "",
            or_(Source.last_import_at.is_(None), Source.last_import_at < cutoff),
        )
        .order_by(Source.last_import_at.asc().nulls_first(), Source.id)
        .limit(limit)
    )
    refreshed = 0
    failed = 0
    for source in rows.scalars():
        if await refresh_source(session, source):
            refreshed += 1
        else:
            failed += 1
        await session.commit()
    return {"refreshed": refreshed, "failed": failed}
