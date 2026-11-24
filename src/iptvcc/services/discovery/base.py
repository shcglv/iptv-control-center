"""Adapter contract shared by every discovery source."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class DiscoveredItem:
    """One link plus enough context to explain where it came from."""

    url: str
    kind: str
    has_credentials: bool
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    snippet: str | None = None
    confidence: float = 0.5
    provenance: dict[str, Any] = field(default_factory=dict)


class DiscoveryAdapter(Protocol):
    """A discovery source. Implementations fetch their own content through ``netguard``."""

    name: str

    async def discover(self, url: str, *, limit: int = 50) -> list[DiscoveredItem]: ...


CONFIDENCE = {"xtream": 0.65, "m3u": 0.55, "epg": 0.45, "unknown": 0.3}
