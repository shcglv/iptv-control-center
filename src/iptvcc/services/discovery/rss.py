"""RSS/Atom adapter: the cheapest structured path when a forum offers feeds."""

from __future__ import annotations

from lxml import etree

from ...netguard import fetch_text
from .base import CONFIDENCE, DiscoveredItem
from .extract import extract_links

MAX_FEED_BYTES = 8 * 1024 * 1024


def _localname(element: etree._Element) -> str:
    return etree.QName(element).localname


def _child_text(element: etree._Element, name: str) -> str | None:
    for child in element.iter():
        if child is element:
            continue
        if _localname(child) == name and child.text:
            return child.text.strip()
    return None


def _atom_link(element: etree._Element) -> str | None:
    for child in element.iter():
        if _localname(child) == "link" and child.get("href"):
            rel = child.get("rel") or "alternate"
            if rel == "alternate":
                return child.get("href")
    return None


def parse_feed(text: str, *, feed_url: str, limit: int) -> list[DiscoveredItem]:
    root = etree.fromstring(
        text.encode("utf-8"), parser=etree.XMLParser(resolve_entities=False, no_network=True)
    )
    items: list[DiscoveredItem] = []
    seen: set[str] = set()

    for element in root.iter():
        if _localname(element) not in {"item", "entry"}:
            continue
        title = _child_text(element, "title")
        link = _child_text(element, "link") or _atom_link(element)
        author = (
            _child_text(element, "creator")
            or _child_text(element, "author")
            or _child_text(element, "name")
        )
        published = (
            _child_text(element, "pubDate")
            or _child_text(element, "published")
            or _child_text(element, "updated")
        )
        body = " ".join(
            filter(
                None,
                [
                    _child_text(element, "description"),
                    _child_text(element, "summary"),
                    _child_text(element, "encoded"),
                    _child_text(element, "content"),
                ],
            )
        )

        provenance = {
            "feed": feed_url,
            "item_title": title,
            "item_link": link,
            "published": published,
        }
        links = extract_links(f"{title or ''}\n{body}", limit=limit)
        if not links and link:
            links = extract_links(link, limit=1)
        for extracted in links:
            if extracted.url in seen:
                continue
            seen.add(extracted.url)
            items.append(
                DiscoveredItem(
                    url=extracted.url,
                    kind=extracted.kind,
                    has_credentials=extracted.has_credentials,
                    title=title,
                    author=author,
                    published_at=published,
                    snippet=(body or "")[:400] or None,
                    confidence=CONFIDENCE.get(extracted.kind, 0.3),
                    provenance=provenance,
                )
            )
            if len(items) >= limit:
                return items
    return items


class RssAdapter:
    name = "rss"

    async def discover(self, url: str, *, limit: int = 50) -> list[DiscoveredItem]:
        body = await fetch_text(url, max_bytes=MAX_FEED_BYTES)
        return parse_feed(body, feed_url=url, limit=limit)
