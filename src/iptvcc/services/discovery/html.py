"""HTML adapter: the fallback for forums without feeds.

It reads the page the operator points at (usually a thread), harvests links from anchors, code
blocks and the page text, and follows ``rel=next`` pagination a bounded number of times.
"""

from __future__ import annotations

from typing import cast
from urllib.parse import urljoin

import lxml.html

from ...netguard import fetch_text
from .base import CONFIDENCE, DiscoveredItem
from .extract import extract_links

MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PAGES = 3

_NEXT_SELECTOR = (
    "//a[@rel='next'] | //link[@rel='next'] | "
    "//a[contains(@class, 'next')] | //a[contains(@class, 'page-numbers')]"
)


def _next_page(root: lxml.html.HtmlElement, base: str) -> str | None:
    for element in cast(list[lxml.html.HtmlElement], root.xpath(_NEXT_SELECTOR)):
        href = element.get("href")
        if href and href not in {"#", "javascript:void(0)"}:
            return str(urljoin(base, href))
    return None


def parse_page(html: str, *, page_url: str, limit: int) -> tuple[list[DiscoveredItem], str | None]:
    root = cast(lxml.html.HtmlElement, lxml.html.fromstring(html, base_url=page_url))
    title = (root.findtext(".//title") or "").strip() or None
    items: list[DiscoveredItem] = []
    seen: set[str] = set()

    def add(url: str, *, origin: str) -> None:
        for extracted in extract_links(url, limit=5):
            if extracted.url in seen:
                continue
            seen.add(extracted.url)
            items.append(
                DiscoveredItem(
                    url=extracted.url,
                    kind=extracted.kind,
                    has_credentials=extracted.has_credentials,
                    title=title,
                    confidence=CONFIDENCE.get(extracted.kind, 0.3),
                    provenance={"page": page_url, "origin": origin},
                )
            )

    # Anchors may point straight at a playlist.
    for anchor in cast(list[lxml.html.HtmlElement], root.xpath("//a[@href]")):
        href = anchor.get("href")
        if href:
            add(urljoin(page_url, href), origin="anchor")

    # Code blocks are where forums keep ready-to-paste playlists.
    for block in cast(list[lxml.html.HtmlElement], root.xpath("//pre | //code | //textarea")):
        text = block.text_content()
        if text and len(text) < 200_000:
            add(text, origin="code")

    # Plain text catches pasted URLs that never became links.
    body_text = root.text_content()
    add(body_text[:200_000], origin="text")

    unique: list[DiscoveredItem] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        unique.append(item)
        if len(unique) >= limit:
            break

    return unique, _next_page(root, page_url)


class HtmlAdapter:
    name = "html"

    def __init__(self, max_pages: int = MAX_PAGES) -> None:
        self._max_pages = max_pages

    async def discover(self, url: str, *, limit: int = 50) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        page_url: str | None = url
        for _ in range(self._max_pages):
            if page_url is None or len(items) >= limit:
                break
            body = await fetch_text(page_url, max_bytes=MAX_PAGE_BYTES)
            page_items, next_url = parse_page(body, page_url=page_url, limit=limit - len(items))
            for item in page_items:
                if item.url in seen:
                    continue
                seen.add(item.url)
                items.append(item)
                if len(items) >= limit:
                    break
            page_url = next_url
        return items[:limit]
