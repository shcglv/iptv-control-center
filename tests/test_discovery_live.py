"""Real-world discovery: the extractors against live markup.

Opt-in, because the pages belong to someone else and their markup changes:

    $env:IPTV_NETWORK_TESTS = "1"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_discovery_live.py -q
"""

from __future__ import annotations

import os

import pytest

from iptvcc.netguard import fetch_text
from iptvcc.services.discovery.html import MAX_PAGE_BYTES, parse_page
from iptvcc.services.discovery.rss import MAX_FEED_BYTES, parse_feed

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("IPTV_NETWORK_TESTS") != "1",
        reason="set IPTV_NETWORK_TESTS=1 to run checks that need the internet",
    ),
]

REPO_PAGE = "https://github.com/iptv-org/iptv"
RELEASES_FEED = "https://github.com/iptv-org/iptv/releases.atom"


@pytest.mark.asyncio
async def test_a_real_page_yields_playlist_links() -> None:
    html = await fetch_text(REPO_PAGE, max_bytes=MAX_PAGE_BYTES)
    items, next_page = parse_page(html, page_url=REPO_PAGE, limit=20)

    assert items, "no items extracted from a page that links to playlists"
    # Kinds are m3u / xtream / epg; the repository page links to m3u files.
    assert any(item.kind in {"m3u", "xtream"} for item in items), [item.kind for item in items]
    assert all(item.url.startswith(("http://", "https://")) for item in items)
    assert next_page is None or next_page.startswith(("http://", "https://"))


@pytest.mark.asyncio
async def test_a_real_atom_feed_parses_without_inventing_items() -> None:
    body = await fetch_text(RELEASES_FEED, max_bytes=MAX_FEED_BYTES)
    assert body, "the feed answered with an empty body"

    items = parse_feed(body, feed_url=RELEASES_FEED, limit=20)
    # Release entries are not playlists; the parser must return nothing rather than garbage.
    assert isinstance(items, list)
    assert all(item.url.startswith(("http://", "https://")) for item in items)
