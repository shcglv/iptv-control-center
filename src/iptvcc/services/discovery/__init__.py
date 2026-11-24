"""Source discovery: extractors, adapters and the candidate store."""

from .base import CONFIDENCE, DiscoveredItem, DiscoveryAdapter
from .extract import ExtractedLink, extract_links
from .html import HtmlAdapter, parse_page
from .rss import RssAdapter, parse_feed
from .store import StoreStats, dedupe_hash, store_items

ADAPTERS = {"rss": RssAdapter, "html": HtmlAdapter}

__all__ = [
    "ADAPTERS",
    "CONFIDENCE",
    "DiscoveredItem",
    "DiscoveryAdapter",
    "ExtractedLink",
    "HtmlAdapter",
    "RssAdapter",
    "StoreStats",
    "dedupe_hash",
    "extract_links",
    "parse_feed",
    "parse_page",
    "store_items",
]
