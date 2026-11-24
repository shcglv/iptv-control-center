"""Discovery: extractors, feed/page adapters and the candidate review flow."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from iptvcc.services.discovery import extract_links, parse_feed, parse_page

from .conftest import ADMIN_HEADERS

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Forum feeds</title>
    <item>
      <title>UK playlist, updated daily</title>
      <link>http://forum.test/t/1284</link>
      <dc:creator>alice</dc:creator>
      <pubDate>Mon, 24 Nov 2026 10:00:00 GMT</pubDate>
      <description>Grab it here: http://provider.test/uk.m3u8</description>
    </item>
    <item>
      <title>Panel access</title>
      <link>http://forum.test/t/1285</link>
      <description>http://panel.test/get.php?username=bob&amp;password=secret</description>
    </item>
  </channel>
</rss>
"""

PAGE = """<!doctype html>
<html><head><title>IPTV thread</title><link rel="next" href="/page/2"></head>
<body>
  <h1>Thread</h1>
  <p>Direct link: <a href="http://provider.test/list.m3u">list</a></p>
  <pre>http://panel.test/get.php?username=bob&amp;password=secret</pre>
  <p>Or paste http://provider.test/other.m3u8 into VLC.</p>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = FEED if self.path.startswith("/feed") else PAGE
        content_type = "application/rss+xml" if self.path.startswith("/feed") else "text/html"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def forum() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


# --- extractors --------------------------------------------------------------------------------


def test_extracts_playlist_and_xtream_with_masking() -> None:
    text = (
        "Playlist: http://provider.test/uk.m3u8\n"
        "Panel: http://panel.test/get.php?username=bob&password=secret\n"
        "API: http://panel.test/player_api.php?username=bob\n"
    )
    links = extract_links(text)
    kinds = {link.kind for link in links}
    assert "m3u" in kinds
    assert "xtream" in kinds

    credentialled = next(link for link in links if link.has_credentials)
    assert credentialled.kind == "xtream"
    assert "secret" not in credentialled.masked
    assert "bob" not in credentialled.masked


def test_extracts_from_extm3u_blob() -> None:
    blob = (
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-id="a",A\nhttp://prov.test/a.ts\n'
        "#EXTINF:-1,B\nhttp://prov.test/b.m3u8\n"
    )
    links = extract_links(f"Look:\n\n{blob}\n\nthanks")
    urls = {link.url for link in links}
    assert "http://prov.test/a.ts" in urls
    assert "http://prov.test/b.m3u8" in urls


def test_deduplicates_urls() -> None:
    text = "http://provider.test/uk.m3u8 and again http://provider.test/uk.m3u8"
    assert len(extract_links(text)) == 1


def test_ignores_unrelated_urls() -> None:
    text = "See https://example.com/page and https://example.com/logo.png"
    assert extract_links(text) == []


# --- adapters ----------------------------------------------------------------------------------


def test_parse_feed_keeps_provenance() -> None:
    items = parse_feed(FEED, feed_url="http://forum.test/feed.xml", limit=10)
    assert len(items) == 2
    first = items[0]
    assert first.url == "http://provider.test/uk.m3u8"
    assert first.title == "UK playlist, updated daily"
    assert first.author == "alice"
    assert first.provenance["feed"] == "http://forum.test/feed.xml"
    assert items[1].has_credentials is True


def test_parse_page_collects_anchors_code_and_text() -> None:
    items, next_page = parse_page(PAGE, page_url="http://forum.test/t/1", limit=10)
    urls = {item.url for item in items}
    assert "http://provider.test/list.m3u" in urls
    assert "http://provider.test/other.m3u8" in urls
    assert any(item.has_credentials for item in items)
    assert next_page == "http://forum.test/page/2"
    origins = {item.provenance["origin"] for item in items}
    assert {"anchor", "code", "text"} <= origins


# --- API ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_flow_creates_reviews_and_promotes(
    client: httpx.AsyncClient, forum: str
) -> None:
    run = await client.post(
        "/api/v1/discovery/run",
        json={"adapter": "rss", "url": f"{forum}/feed.xml", "limit": 20},
        headers=ADMIN_HEADERS,
    )
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["created"] == 2
    assert body["updated"] == 0

    listed = await client.get("/api/v1/discovery/candidates", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 2
    masked = [item["url_masked"] for item in page["items"]]
    assert all("secret" not in value and "bob" not in value for value in masked)

    # Re-running the same feed must not duplicate anything.
    again = await client.post(
        "/api/v1/discovery/run",
        json={"adapter": "rss", "url": f"{forum}/feed.xml", "limit": 20},
        headers=ADMIN_HEADERS,
    )
    assert again.json()["created"] == 0
    assert again.json()["updated"] == 2

    credentialled = next(item for item in page["items"] if item["has_credentials"])
    accepted = await client.post(
        f"/api/v1/discovery/candidates/{credentialled['id']}/accept",
        json={"name": "Discovered panel"},
        headers=ADMIN_HEADERS,
    )
    assert accepted.status_code == 200, accepted.text
    source = accepted.json()
    assert source["name"] == "Discovered panel"
    assert source["type"] == "xtream"

    other = next(item for item in page["items"] if not item["has_credentials"])
    rejected = await client.post(
        f"/api/v1/discovery/candidates/{other['id']}/reject", headers=ADMIN_HEADERS
    )
    assert rejected.status_code == 204

    status = (await client.get("/api/v1/discovery/status", headers=ADMIN_HEADERS)).json()
    assert status["accepted"] == 1
    assert status["rejected"] == 1
    assert set(status["adapters"]) == {"rss", "html"}


@pytest.mark.asyncio
async def test_html_adapter_via_api(client: httpx.AsyncClient, forum: str) -> None:
    run = await client.post(
        "/api/v1/discovery/run",
        json={"adapter": "html", "url": f"{forum}/thread/1", "limit": 20},
        headers=ADMIN_HEADERS,
    )
    assert run.status_code == 200, run.text
    assert run.json()["created"] >= 2


@pytest.mark.asyncio
async def test_accepting_twice_is_rejected(client: httpx.AsyncClient, forum: str) -> None:
    await client.post(
        "/api/v1/discovery/run",
        json={"adapter": "rss", "url": f"{forum}/feed.xml", "limit": 20},
        headers=ADMIN_HEADERS,
    )
    page = (await client.get("/api/v1/discovery/candidates", headers=ADMIN_HEADERS)).json()
    candidate_id = page["items"][0]["id"]
    first = await client.post(
        f"/api/v1/discovery/candidates/{candidate_id}/accept", json={}, headers=ADMIN_HEADERS
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/discovery/candidates/{candidate_id}/accept", json={}, headers=ADMIN_HEADERS
    )
    assert second.status_code == 409
