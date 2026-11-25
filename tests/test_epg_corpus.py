"""XMLTV corpus: what providers actually emit, and what must not crash the import."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import EpgChannel, EpgSource, Programme
from iptvcc.services.epg import (
    XmltvError,
    format_xmltv_time,
    ingest_xmltv,
    iter_xmltv,
    parse_xmltv_time,
)

# 2026-11-25 18:00:00 as UTC epoch
UTC_18 = int(datetime(2026, 11, 25, 18, 0, tzinfo=UTC).timestamp())


def _doc(body: str, *, declaration: str = '<?xml version="1.0" encoding="UTF-8"?>') -> str:
    return f"{declaration}\n<tv>\n{body}\n</tv>"


# --- timestamps --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20261125180000 +0000", UTC_18),
        ("20261125180000 +0300", UTC_18 - 3 * 3600),
        ("20261125180000 -0500", UTC_18 + 5 * 3600),
        ("20261125180000+0000", UTC_18),
        ("20261125180000 +03:00", UTC_18 - 3 * 3600),  # colon form, seen in the wild
        ("20261125180000", UTC_18 - 3 * 3600),  # no offset -> Europe/Moscow default
    ],
)
def test_offsets_and_shapes(value: str, expected: int) -> None:
    assert parse_xmltv_time(value, "Europe/Moscow") == expected


def test_short_timestamps_are_padded() -> None:
    # Some panels emit a date only, or drop the seconds.
    assert parse_xmltv_time("20261125", "UTC") == int(
        datetime(2026, 11, 25, tzinfo=UTC).timestamp()
    )
    assert parse_xmltv_time("202611251800", "UTC") == UTC_18


def test_bad_timestamp_raises() -> None:
    with pytest.raises(XmltvError):
        parse_xmltv_time("not-a-date", "UTC")
    with pytest.raises(XmltvError):
        parse_xmltv_time("20261125180000 +abc", "UTC")


def test_format_round_trip() -> None:
    assert format_xmltv_time(UTC_18) == "20261125180000 +0000"
    assert parse_xmltv_time(format_xmltv_time(UTC_18), "UTC") == UTC_18


# --- document shapes ---------------------------------------------------------------------------


def test_doctype_is_allowed_and_predefined_entities_work() -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
        '<tv><channel id="a.test"><display-name>A &amp; B</display-name></channel>'
        '<programme start="20261125180000 +0000" stop="20261125190000 +0000" channel="a.test">'
        "<title>News &amp; Weather</title></programme></tv>"
    )
    records = list(iter_xmltv(document, "UTC"))
    kinds = [kind for kind, _ in records]
    assert kinds == ["channel", "programme"]
    assert records[0][1]["display_name"] == "A & B"
    assert records[1][1]["title"] == "News & Weather"


def test_xml_declaration_with_windows_1251_does_not_mojibake() -> None:
    document = (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        '<tv><channel id="one.ru"><display-name>Первый канал</display-name></channel></tv>'
    )
    records = list(iter_xmltv(document, "UTC"))
    assert records[0][1]["display_name"] == "Первый канал"


def test_bom_and_crlf_are_tolerated() -> None:
    document = (
        '\ufeff<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<tv>\r\n<channel id="a"><display-name>A</display-name></channel>\r\n</tv>'
    )
    assert len(list(iter_xmltv(document, "UTC"))) == 1


def test_cdata_and_numeric_entities() -> None:
    document = _doc(
        '<channel id="a"><display-name><![CDATA[A &amp; B]]></display-name></channel>'
        '<programme start="20261125180000 +0000" channel="a">'
        "<title><![CDATA[Tom &#38; Jerry]]></title></programme>"
    )
    records = list(iter_xmltv(document, "UTC"))
    # CDATA is literal text: entities inside it are not expanded, and must be passed through as-is.
    assert records[0][1]["display_name"] == "A &amp; B"
    assert records[1][1]["title"] == "Tom &#38; Jerry"


def test_programme_without_stop_is_kept() -> None:
    document = _doc(
        '<channel id="a"><display-name>A</display-name></channel>'
        '<programme start="20261125180000 +0000" channel="a"><title>Open ended</title></programme>'
    )
    records = list(iter_xmltv(document, "UTC"))
    assert records[1][1]["stop"] is None


def test_programme_with_bad_time_is_skipped_not_fatal() -> None:
    document = _doc(
        '<channel id="a"><display-name>A</display-name></channel>'
        '<programme start="garbage" channel="a"><title>Bad</title></programme>'
        '<programme start="20261125180000 +0000" channel="a"><title>Good</title></programme>'
    )
    records = [record for kind, record in iter_xmltv(document, "UTC") if kind == "programme"]
    assert [record["title"] for record in records] == ["Good"]


def test_programme_before_channel_is_yielded_and_skipped_by_the_importer() -> None:
    """The parser is document-order; deciding that an orphan programme is unusable is ingest's job."""
    document = _doc(
        '<programme start="20261125180000 +0000" channel="late"><title>Early</title></programme>'
        '<channel id="late"><display-name>Late</display-name></channel>'
    )
    records = list(iter_xmltv(document, "UTC"))
    assert [kind for kind, _ in records] == ["programme", "channel"]


# --- ingest ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_channel_ids_are_merged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = _doc(
        '<channel id="dup"><display-name>First</display-name></channel>'
        '<channel id="dup"><display-name>Second</display-name></channel>'
        '<programme start="20261125180000 +0000" channel="dup"><title>T</title></programme>'
    )
    async with session_factory() as session:
        source = EpgSource(name="E", default_timezone="UTC")
        session.add(source)
        await session.flush()
        stats = await ingest_xmltv(session, source, document)
        await session.commit()
        assert stats.channels == 1
        assert stats.programmes == 1
        channel = await session.scalar(
            __import__("sqlalchemy").select(EpgChannel).where(EpgChannel.source_tvg_id == "dup")
        )
        assert channel is not None
        assert channel.display_name == "Second"


@pytest.mark.asyncio
async def test_reimport_replaces_programmes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = _doc(
        '<channel id="a"><display-name>A</display-name></channel>'
        '<programme start="20261125180000 +0000" channel="a"><title>One</title></programme>'
    )
    updated = _doc(
        '<channel id="a"><display-name>A</display-name></channel>'
        '<programme start="20261125180000 +0000" channel="a"><title>Two</title></programme>'
    )
    async with session_factory() as session:
        source = EpgSource(name="E", default_timezone="UTC")
        session.add(source)
        await session.flush()
        await ingest_xmltv(session, source, document)
        await ingest_xmltv(session, source, updated)
        await session.commit()

        from sqlalchemy import func, select

        total = await session.scalar(select(func.count()).select_from(Programme))
        assert total == 1
        title = await session.scalar(select(Programme.title))
        assert title == "Two"


@pytest.mark.asyncio
async def test_programmes_for_unknown_channels_are_counted_as_skipped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = _doc(
        '<channel id="a"><display-name>A</display-name></channel>'
        '<programme start="20261125180000 +0000" channel="ghost"><title>Orphan</title></programme>'
    )
    async with session_factory() as session:
        source = EpgSource(name="E", default_timezone="UTC")
        session.add(source)
        await session.flush()
        stats = await ingest_xmltv(session, source, document)
        await session.commit()
        assert stats.skipped == 1
        assert stats.programmes == 0
