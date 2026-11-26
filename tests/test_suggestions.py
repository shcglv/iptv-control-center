"""Near-duplicate detection, review and merge."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import Channel, ChannelStream, MatchSuggestion
from iptvcc.services.importing import import_playlist
from iptvcc.services.suggestions import merge_channels, scan_for_duplicates

from .conftest import ADMIN_HEADERS

# "Eurosport 1" and "Eurosport 2" normalise to keys that score in the review band (78-92%).
SIMILAR = (
    "#EXTM3U\n"
    '#EXTINF:-1 tvg-id="euro1.uk",Eurosport 1\nhttp://p.test/euro1.ts\n'
    '#EXTINF:-1 tvg-id="euro2.uk",Eurosport 2\nhttp://p.test/euro2.ts\n'
    '#EXTINF:-1 tvg-id="cnn.us",CNN\nhttp://p.test/cnn.ts\n'
)


async def _seed(session: AsyncSession) -> None:
    from iptvcc.models import Source

    source = Source(name="Dup", url="http://p.test/l.m3u")
    session.add(source)
    await session.flush()
    await import_playlist(session, source, SIMILAR)
    await session.commit()


# --- detection ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_finds_a_near_duplicate_pair(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed(session)
        stats = await scan_for_duplicates(session)
        await session.commit()

        assert stats.created == 1
        suggestion = await session.scalar(select(MatchSuggestion))
        assert suggestion is not None
        assert 78 <= suggestion.score < 92
        names = {row.id: row.display_name for row in (await session.scalars(select(Channel))).all()}
        pair = {names[suggestion.keep_id], names[suggestion.drop_id]}
        assert pair == {"Eurosport 1", "Eurosport 2"}


@pytest.mark.asyncio
async def test_scan_is_idempotent(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await _seed(session)
        await scan_for_duplicates(session)
        await session.commit()
        second = await scan_for_duplicates(session)
        await session.commit()
        assert second.created == 0
        assert second.skipped >= 1


@pytest.mark.asyncio
async def test_identical_names_are_not_suggested(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Import already merges identical normalised names; a suggestion would be noise."""
    from iptvcc.models import Source

    async with session_factory() as session:
        source = Source(name="Same", url="http://p.test/s.m3u")
        session.add(source)
        await session.flush()
        await import_playlist(
            session,
            source,
            '#EXTM3U\n#EXTINF:-1 tvg-id="a",BBC One HD\nhttp://p.test/a.ts\n'
            '#EXTINF:-1 tvg-id="b",BBC One\nhttp://p.test/b.ts\n',
        )
        await session.commit()
        stats = await scan_for_duplicates(session)
        await session.commit()
        assert stats.created == 0


# --- merge -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_moves_links_and_deletes_the_duplicate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed(session)
        await scan_for_duplicates(session)
        await session.commit()
        suggestion = await session.scalar(select(MatchSuggestion))
        assert suggestion is not None
        keep_id, drop_id = suggestion.keep_id, suggestion.drop_id

        await merge_channels(session, suggestion)
        await session.commit()

        assert await session.get(Channel, drop_id) is None
        kept_links = await session.scalar(
            select(func.count())
            .select_from(ChannelStream)
            .where(ChannelStream.channel_id == keep_id)
        )
        assert kept_links == 2  # its own link plus the one rescued from the duplicate
        # The resolved suggestion is removed with the duplicate: the queue is open decisions only.
        assert await session.get(MatchSuggestion, suggestion.id) is None


@pytest.mark.asyncio
async def test_merge_is_not_repeatable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed(session)
        await scan_for_duplicates(session)
        await session.commit()
        suggestion = await session.scalar(select(MatchSuggestion))
        assert suggestion is not None
        await merge_channels(session, suggestion)
        await session.commit()

        from iptvcc.services.suggestions import merge_channels as merge_again

        with pytest.raises(ValueError, match="no longer exists"):
            await merge_again(session, suggestion)


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_api_flow(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Dup", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SIMILAR}, headers=ADMIN_HEADERS
    )

    scan = await client.post("/api/v1/matches/scan", headers=ADMIN_HEADERS)
    assert scan.status_code == 200, scan.text
    assert scan.json()["created"] == 1

    listed = (await client.get("/api/v1/matches", headers=ADMIN_HEADERS)).json()
    assert len(listed) == 1
    suggestion = listed[0]
    assert {suggestion["keep_name"], suggestion["drop_name"]} == {"Eurosport 1", "Eurosport 2"}

    accepted = await client.post(
        f"/api/v1/matches/{suggestion['id']}/accept", headers=ADMIN_HEADERS
    )
    assert accepted.status_code == 204, accepted.text

    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    names = {row["name"] for row in page["items"]}
    assert "Eurosport 2" not in names
    assert "Eurosport 1" in names

    again = await client.post(f"/api/v1/matches/{suggestion['id']}/accept", headers=ADMIN_HEADERS)
    # The suggestion is gone once resolved, so a second accept is a 404 rather than a 409.
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_reject_keeps_both_channels(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Dup", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SIMILAR}, headers=ADMIN_HEADERS
    )
    await client.post("/api/v1/matches/scan", headers=ADMIN_HEADERS)
    suggestion = (await client.get("/api/v1/matches", headers=ADMIN_HEADERS)).json()[0]

    rejected = await client.post(
        f"/api/v1/matches/{suggestion['id']}/reject", headers=ADMIN_HEADERS
    )
    assert rejected.status_code == 204

    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    assert page["total"] == 3
    assert (await client.get("/api/v1/matches", headers=ADMIN_HEADERS)).json() == []
