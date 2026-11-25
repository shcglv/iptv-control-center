"""XMLTV ingestion and per-device guide rendering.

Ingestion streams the document with ``iterparse`` and inserts in batches, so memory stays flat
regardless of file size. Programmes are stored as UTC epoch seconds.

A source is fully replaced on every import: programmes for that source are deleted first, which
makes re-import idempotent and implements retention in the same pass.
"""

from __future__ import annotations

import gzip
import hashlib
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lxml import etree
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import __version__
from ..matching import normalize
from ..models import Channel, ChannelEpgMap, EpgChannel, EpgSource, Programme
from .delivery import get_active_assignment
from .profiles import visible_channels

BATCH_SIZE = 2000
_GUIDE_CHUNK = 200
MAX_DOCUMENT_BYTES = 512 * 1024 * 1024
_XMLTV_FORMAT = "%Y%m%d%H%M%S"
_OUTPUT_FORMAT = "%Y%m%d%H%M%S +0000"


class XmltvError(ValueError):
    """Raised when a document is not safe or not XMLTV."""


@dataclass
class EpgIngestStats:
    channels: int = 0
    programmes: int = 0
    skipped: int = 0
    pruned: int = 0
    mapped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "channels": self.channels,
            "programmes": self.programmes,
            "skipped": self.skipped,
            "pruned": self.pruned,
            "mapped": self.mapped,
        }


def parse_xmltv_time(value: str, default_tz: str) -> int:
    """Parse ``YYYYMMDDhhmmss [+HHMM]`` into UTC epoch seconds."""
    raw = (value or "").strip()
    if not raw:
        raise XmltvError("empty datetime")
    parts = raw.split()
    try:
        naive = datetime.strptime(parts[0], _XMLTV_FORMAT)
    except ValueError as exc:
        raise XmltvError(f"bad XMLTV datetime: {value!r}") from exc
    if len(parts) > 1:
        offset = parts[1]
        try:
            sign = -1 if offset.startswith("-") else 1
            digits = offset.lstrip("+-")
            hours, minutes = int(digits[:2]), int(digits[2:4] or 0)
        except (ValueError, IndexError) as exc:
            raise XmltvError(f"bad XMLTV offset: {offset!r}") from exc
        return int(naive.replace(tzinfo=UTC).timestamp()) - sign * (hours * 3600 + minutes * 60)
    try:
        zone: tzinfo = ZoneInfo(default_tz)
    except Exception:
        zone = UTC
    return int(naive.replace(tzinfo=zone).timestamp())


_OUTPUT_FORMAT = "%Y%m%d%H%M%S +0000"


def format_xmltv_time(epoch: int) -> str:
    # Hand-built from gmtime: datetime.fromtimestamp + strftime costs ~20 us per call on Windows,
    # and a guide formats two timestamps per programme.
    tm = time.gmtime(epoch)
    return (
        f"{tm.tm_year:04d}{tm.tm_mon:02d}{tm.tm_mday:02d}"
        f"{tm.tm_hour:02d}{tm.tm_min:02d}{tm.tm_sec:02d} +0000"
    )


def _guard_document(data: bytes) -> None:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise XmltvError("document is too large")
    head = data[:4096].lower()
    if b"<!doctype" in head:
        # External entity expansion is never needed for XMLTV; refuse rather than harden.
        raise XmltvError("DOCTYPE declarations are not allowed")


def _release(element: etree._Element) -> None:
    """Free a processed subtree: clearing children is enough, the parent goes too."""
    element.clear()
    parent = element.getparent()
    if parent is None:
        return
    while element.getprevious() is not None:
        del parent[0]


def iter_xmltv(data: bytes | str, default_tz: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``("channel", {...})`` and ``("programme", {...})`` records in document order.

    Only the elements we consume are released. Clearing every element end would wipe
    ``<display-name>`` before its parent ``<channel>`` is read.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    _guard_document(data)
    context = etree.iterparse(
        BytesIO(data), events=("end",), huge_tree=False, no_network=True, recover=False
    )
    for _event, element in context:
        tag = etree.QName(element).localname
        if tag == "channel":
            channel_id = element.get("id")
            if channel_id:
                display = element.findtext("display-name") or ""
                icon = element.find("icon")
                yield (
                    "channel",
                    {
                        "id": channel_id,
                        "display_name": display.strip(),
                        "icon_url": icon.get("src") if icon is not None else None,
                    },
                )
            _release(element)
        elif tag == "programme":
            start = element.get("start")
            channel_id = element.get("channel")
            if start and channel_id:
                stop = element.get("stop")
                try:
                    start_epoch = parse_xmltv_time(start, default_tz)
                    stop_epoch = parse_xmltv_time(stop, default_tz) if stop else None
                except XmltvError:
                    start_epoch = None
                    stop_epoch = None
                if start_epoch is not None:
                    yield (
                        "programme",
                        {
                            "channel": channel_id,
                            "start": start_epoch,
                            "stop": stop_epoch,
                            "title": (element.findtext("title") or "").strip(),
                            "subtitle": (element.findtext("sub-title") or "").strip() or None,
                            "description": (element.findtext("desc") or "").strip() or None,
                            "category": (element.findtext("category") or "").strip() or None,
                        },
                    )
            _release(element)


async def ingest_xmltv(
    session: AsyncSession, source: EpgSource, data: bytes | str
) -> EpgIngestStats:
    stats = EpgIngestStats()
    cutoff = int(time.time()) - source.retention_days * 86_400

    existing = {
        row.source_tvg_id: row
        for row in (
            await session.scalars(select(EpgChannel).where(EpgChannel.epg_source_id == source.id))
        ).all()
    }
    if existing:
        await session.execute(
            delete(Programme).where(
                Programme.epg_channel_id.in_([row.id for row in existing.values()])
            )
        )

    batch: list[dict[str, object]] = []

    async def drain() -> None:
        if batch:
            await session.execute(insert(Programme), batch)
            batch.clear()

    for kind, item in iter_xmltv(data, source.default_timezone):
        if kind == "channel":
            channel = existing.get(item["id"])
            if channel is None:
                channel = EpgChannel(
                    epg_source_id=source.id,
                    source_tvg_id=item["id"],
                    display_name=item["display_name"],
                    icon_url=item["icon_url"],
                )
                session.add(channel)
                # No flush here: a guide lists thousands of channels before the first programme.
                # The id is assigned when a programme needs it (one flush for all pending rows).
                existing[item["id"]] = channel
                stats.channels += 1
            else:
                channel.display_name = item["display_name"] or channel.display_name
                channel.icon_url = item["icon_url"] or channel.icon_url
            continue

        channel = existing.get(item["channel"])
        if channel is None:
            stats.skipped += 1
            continue
        if channel.id is None:
            # First programme of a channel created in this run: assign ids to every pending row.
            await session.flush()
        if item["stop"] is not None and item["stop"] < cutoff:
            stats.pruned += 1
            continue
        batch.append(
            {
                "epg_channel_id": channel.id,
                "start_epoch": item["start"],
                "stop_epoch": item["stop"],
                "title": item["title"],
                "subtitle": item["subtitle"],
                "description": item["description"],
                "category": item["category"],
            }
        )
        stats.programmes += 1
        if len(batch) >= BATCH_SIZE:
            await drain()

    await drain()
    await session.flush()

    source.last_fetch_at = datetime.now(UTC)
    source.last_status = "success"
    source.last_error = None
    stats.mapped = await map_channels(session)
    return stats


async def map_channels(session: AsyncSession) -> int:
    """Attach catalog channels to EPG channels: exact ``tvg_id`` first, then normalised name."""
    channels = (await session.scalars(select(Channel))).all()
    epg_channels = list(
        (await session.scalars(select(EpgChannel).options(selectinload(EpgChannel.source)))).all()
    )
    epg_channels.sort(key=lambda row: (-row.source.priority, row.id))

    by_tvg: dict[str, EpgChannel] = {}
    by_name: dict[str, EpgChannel] = {}
    for row in epg_channels:
        by_tvg.setdefault(row.source_tvg_id.lower(), row)
        if row.display_name:
            by_name.setdefault(normalize(row.display_name), row)

    existing = {row.channel_id: row for row in (await session.scalars(select(ChannelEpgMap))).all()}

    mapped = 0
    for channel in channels:
        method, confidence = "tvg_id", 1.0
        match = by_tvg.get(channel.tvg_id.lower()) if channel.tvg_id else None
        if match is None:
            method, confidence = "name", 0.9
            match = by_name.get(normalize(channel.display_name))
        if match is None:
            continue

        mapping = existing.get(channel.id)
        if mapping is not None:
            if mapping.is_manual:
                continue
            mapping.epg_channel_id = match.id
            mapping.method = method
            mapping.confidence = confidence
        else:
            session.add(
                ChannelEpgMap(
                    channel_id=channel.id,
                    epg_channel_id=match.id,
                    method=method,
                    confidence=confidence,
                )
            )
        mapped += 1

    await session.flush()
    return mapped


async def epg_ids_for_assignment(
    session: AsyncSession, token: str
) -> list[tuple[str, int, str, str | None]] | None:
    """Return ``(channel_id, epg_channel_id, xmltv_id, display_name)`` for a device's lineup."""
    assignment = await get_active_assignment(session, token)
    if assignment is None:
        return None

    mapping = {
        row[0]: (row[1], row[2])
        for row in (
            await session.execute(
                select(ChannelEpgMap.channel_id, EpgChannel.id, EpgChannel.source_tvg_id).join(
                    EpgChannel, EpgChannel.id == ChannelEpgMap.epg_channel_id
                )
            )
        ).all()
    }

    entries: list[tuple[str, int, str, str | None]] = []
    for item in await visible_channels(session, assignment.playlist_profile_id):
        channel = item.channel
        mapped = mapping.get(channel.id)
        if mapped is None:
            continue
        epg_channel_id, xmltv_id = mapped
        entries.append((channel.id, epg_channel_id, xmltv_id, channel.display_name))
    return entries


def guide_cache_key(token: str) -> str:
    """Filesystem-safe key for a device's rendered guide (never the token itself)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


async def build_guide(
    session: AsyncSession,
    token: str,
    out_dir: Path,
    *,
    days_back: int = 1,
    days_forward: int = 7,
    cache_key: str | None = None,
) -> tuple[Path, Path] | None:
    """Render the device guide to ``out_dir`` and return ``(xml_path, gzip_path)``.

    ``cache_key`` defaults to a hash of the token, but callers that share the rendered file between
    devices on the same playlist profile pass a profile-scoped key: the guide depends on the
    profile, not on the device.
    """
    entries = await epg_ids_for_assignment(session, token)
    if entries is None:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key or guide_cache_key(token)
    xml_path = out_dir / f"{key}.xml"
    gz_path = out_dir / f"{key}.xml.gz"

    now = int(time.time())
    window_start = now - days_back * 86_400
    window_end = now + days_forward * 86_400

    with etree.xmlfile(str(xml_path), encoding="utf-8") as xf:
        xf.write_declaration()
        with xf.element("tv", {"generator-info-name": f"iptv-control-center/{__version__}"}):
            # XMLTV requires every <channel> before the first <programme>.
            for _channel_id, _epg_id, xmltv_id, display_name in entries:
                channel_el = etree.Element("channel", {"id": xmltv_id})
                etree.SubElement(channel_el, "display-name").text = display_name or xmltv_id
                xf.write(channel_el)

            # Programmes are fetched in chunks of channels: one query per few hundred channels
            # instead of one per channel, and only the columns the writer needs.
            for start in range(0, len(entries), _GUIDE_CHUNK):
                chunk = entries[start : start + _GUIDE_CHUNK]
                ids = [epg_id for _channel_id, epg_id, _xmltv_id, _display_name in chunk]
                rows = (
                    await session.execute(
                        select(
                            Programme.epg_channel_id,
                            Programme.start_epoch,
                            Programme.stop_epoch,
                            Programme.title,
                            Programme.subtitle,
                            Programme.description,
                            Programme.category,
                        )
                        .where(
                            Programme.epg_channel_id.in_(ids),
                            Programme.start_epoch <= window_end,
                            (Programme.stop_epoch.is_(None))
                            | (Programme.stop_epoch >= window_start),
                        )
                        .order_by(Programme.epg_channel_id, Programme.start_epoch)
                    )
                ).all()
                grouped: dict[int, list[Any]] = {}
                for row in rows:
                    grouped.setdefault(row.epg_channel_id, []).append(row)

                for _channel_id, epg_id, xmltv_id, _display_name in chunk:
                    for programme in grouped.get(epg_id, []):
                        attrs = {
                            "start": format_xmltv_time(programme.start_epoch),
                            "channel": xmltv_id,
                        }
                        if programme.stop_epoch is not None:
                            attrs["stop"] = format_xmltv_time(programme.stop_epoch)
                        element = etree.Element("programme", attrs)
                        etree.SubElement(element, "title").text = programme.title or ""
                        if programme.subtitle:
                            etree.SubElement(element, "sub-title").text = programme.subtitle
                        if programme.description:
                            etree.SubElement(element, "desc").text = programme.description
                        if programme.category:
                            etree.SubElement(element, "category").text = programme.category
                        xf.write(element)

    with open(xml_path, "rb") as raw, gzip.open(gz_path, "wb", compresslevel=6) as compressed:
        while block := raw.read(1024 * 1024):
            compressed.write(block)

    return xml_path, gz_path
