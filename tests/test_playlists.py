"""Provider-corpus hardening: realistic and hostile playlists must import correctly."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from iptvcc.m3u import PlaylistError, iter_entries, parse_attributes, validate_playlist
from iptvcc.services.importing import ImportStats, import_playlist

from .conftest import ADMIN_HEADERS

# --- parser shapes -----------------------------------------------------------------------------


def test_single_quoted_attributes_are_parsed() -> None:
    attrs = parse_attributes(
        """tvg-id='bbc1.uk' group-title='News' tvg-logo='http://l.test/x.png'"""
    )
    assert attrs == {"tvg-id": "bbc1.uk", "group-title": "News", "tvg-logo": "http://l.test/x.png"}


def test_unquoted_attributes_are_parsed() -> None:
    attrs = parse_attributes("tvg-id=bbc1.uk group-title=News")
    assert attrs == {"tvg-id": "bbc1.uk", "group-title": "News"}


def test_commas_inside_quotes_do_not_split_the_name() -> None:
    entry = next(
        iter_entries(
            [
                '#EXTINF:-1 tvg-id="x" group-title="News, World",BBC One, HD',
                "http://p.test/1.ts",
            ]
        )
    )
    assert entry.name == "BBC One, HD"
    assert entry.group_title == "News, World"


def test_extgrp_before_extinf_applies_to_the_entry() -> None:
    entry = next(iter_entries(["#EXTGRP:Sport", "#EXTINF:-1,Eurosport", "http://p.test/e.ts"]))
    assert entry.group_title == "Sport"


def test_extgrp_does_not_leak_into_the_next_entry() -> None:
    entries = list(
        iter_entries(
            [
                "#EXTINF:-1,A",
                "#EXTGRP:Sport",
                "http://p.test/a.ts",
                "#EXTINF:-1,B",
                "http://p.test/b.ts",
            ]
        )
    )
    assert entries[0].group_title == "Sport"
    assert entries[1].group_title is None


def test_extvlcopt_and_crlf_and_bom_are_tolerated() -> None:
    payload = (
        "\ufeff#EXTM3U\r\n"
        '#EXTINF:-1 tvg-id="a",Channel A\r\n'
        "#EXTVLCOPT:http-user-agent=VLC/3.0.20\r\n"
        "http://p.test/a.ts\r\n"
    )
    entries = list(iter_entries(payload.splitlines()))
    assert len(entries) == 1
    assert entries[0].url == "http://p.test/a.ts"
    assert entries[0].vlc_opts == ("http-user-agent=VLC/3.0.20",)


def test_url_without_extinf_gets_a_name_from_the_path() -> None:
    entry = next(iter_entries(["http://p.test/live/bbc_one-hd.ts"]))
    assert entry.name == "bbc one hd"


def test_media_playlist_is_rejected_with_an_explanation() -> None:
    with pytest.raises(PlaylistError, match="HLS media playlist"):
        validate_playlist(
            ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10", "#EXTINF:10,", "seg1.ts"]
        )


def test_playlist_without_urls_is_rejected() -> None:
    with pytest.raises(PlaylistError, match="no channel URLs"):
        validate_playlist(["#EXTM3U", '#EXTINF:-1 tvg-id="a",A'])


# --- import behaviour --------------------------------------------------------------------------


async def _import(session, text: str) -> ImportStats:
    from iptvcc.models import Source

    source = Source(name="Corpus", url="http://p.test/l.m3u")
    session.add(source)
    await session.flush()
    return await import_playlist(session, source, text)


@pytest.mark.asyncio
async def test_non_http_urls_are_skipped_not_stored(session_factory) -> None:
    async with session_factory() as session:
        stats = await _import(
            session,
            "#EXTM3U\n"
            "#EXTINF:-1,Multicast\nudp://239.0.0.1:5000\n"
            "#EXTINF:-1,RTMP\nrtmp://p.test/live/x\n"
            "#EXTINF:-1,HTTP\nhttp://p.test/ok.ts\n",
        )
        await session.commit()
        assert stats.parsed == 3
        assert stats.skipped == 2
        assert stats.created == 1


@pytest.mark.asyncio
async def test_same_url_twice_with_different_ids_links_one_stream(session_factory) -> None:
    async with session_factory() as session:
        stats = await _import(
            session,
            '#EXTM3U\n#EXTINF:-1 tvg-id="a",A\nhttp://p.test/same.ts\n'
            '#EXTINF:-1 tvg-id="b",B\nhttp://p.test/same.ts\n',
        )
        await session.commit()
        assert stats.created == 1
        assert stats.channels_created == 2
        assert stats.channels_linked == 2


@pytest.mark.asyncio
async def test_duplicate_tvg_id_merges_into_one_channel(session_factory) -> None:
    async with session_factory() as session:
        stats = await _import(
            session,
            '#EXTM3U\n#EXTINF:-1 tvg-id="same.uk" group-title="A",Same\nhttp://p.test/1.ts\n'
            '#EXTINF:-1 tvg-id="same.uk" group-title="B",Same Backup\nhttp://p.test/2.ts\n',
        )
        await session.commit()
        assert stats.channels_created == 1
        assert stats.created == 2


@pytest.mark.asyncio
async def test_junk_lines_and_comments_are_ignored(session_factory) -> None:
    async with session_factory() as session:
        stats = await _import(
            session,
            "#EXTM3U\n"
            "# provider: example\n"
            "\n"
            "   \n"
            "#EXTINF:-1,A\n"
            "http://p.test/a.ts\n"
            "garbage line without url scheme\n"
            "# another comment\n",
        )
        await session.commit()
        assert stats.parsed == 2  # the EXTINF entry and the bare "garbage..." line
        assert stats.skipped == 1  # the garbage line has no http scheme


@pytest.mark.asyncio
async def test_cyrillic_names_survive_round_trip(session_factory) -> None:
    async with session_factory() as session:
        await _import(session, "#EXTM3U\n#EXTINF:-1,Первый канал\nhttp://p.test/1.ts\n")
        await session.commit()
        from sqlalchemy import select

        from iptvcc.models import Channel

        channel = await session.scalar(select(Channel))
        assert channel is not None
        assert channel.display_name == "Первый канал"


# --- HTTP encodings ----------------------------------------------------------------------------


WINDOWS_1251_PLAYLIST = "#EXTM3U\n#EXTINF:-1,Первый канал\nhttp://p.test/1.ts\n".encode("cp1251")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        # Deliberately no charset: many provider panels send windows-1251 this way.
        self.send_header("Content-Type", "audio/x-mpegurl")
        self.send_header("Content-Length", str(len(WINDOWS_1251_PLAYLIST)))
        self.end_headers()
        self.wfile.write(WINDOWS_1251_PLAYLIST)

    def log_message(self, *_args: object) -> None:
        return


def test_windows_1251_without_charset_is_decoded() -> None:
    import asyncio

    from iptvcc.netguard import fetch_text

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/list.m3u"
        text = asyncio.run(fetch_text(url))
    finally:
        server.shutdown()
        server.server_close()
    assert "Первый канал" in text


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_rejects_a_media_playlist(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources", json={"name": "HLS", "url": "http://p.test/x"}, headers=ADMIN_HEADERS
        )
    ).json()
    response = await client.post(
        f"/api/v1/sources/{source['id']}/import",
        json={"m3u": "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\nseg1.ts\n"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422
    assert "HLS media playlist" in response.json()["detail"]


@pytest.mark.asyncio
async def test_api_rejects_an_empty_playlist(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Empty", "url": "http://p.test/x"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    response = await client.post(
        f"/api/v1/sources/{source['id']}/import",
        json={"m3u": "#EXTM3U\n# nothing here\n"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422
    assert "no channel URLs" in response.json()["detail"]
