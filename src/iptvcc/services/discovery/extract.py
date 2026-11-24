"""Extract IPTV artefacts from arbitrary text (forum posts, feeds, code blocks).

Each extractor returns the URL, a coarse kind, whether it carries credentials, and a masked form.
The raw URL is never logged; only the mask is.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ...secrets import mask_url

_M3U_RE = re.compile(r"https?://[^\s\"'<>()]+\.m3u8?(?:\?[^\s\"'<>()]*)?", re.IGNORECASE)
_XTREAM_GET_RE = re.compile(
    r"https?://[^\s\"'<>]+/get\.php\?(?:[^\s\"'<>]*&)?username=(?P<user>[^&\s\"'<>]+)"
    r"&(?:[^\s\"'<>]*&)?password=(?P<password>[^&\s\"'<>]+)",
    re.IGNORECASE,
)
_XTREAM_API_RE = re.compile(
    r"https?://[^\s\"'<>]+/(?:player_api|panel_api)\.php(?:\?[^\s\"'<>]*)?", re.IGNORECASE
)
_EPG_RE = re.compile(r"https?://[^\s\"'<>]+\.xml(?:\.gz)?(?:\?[^\s\"'<>]*)?", re.IGNORECASE)
_EXTM3U_BLOCK_RE = re.compile(r"(?m)^#EXTM3U\b[\s\S]{0,200000}?(?=^\s*$|\Z)")
_B64_RE = re.compile(
    r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{4}){16,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)
_MAX_PER_BLOCK = 5


@dataclass(frozen=True)
class ExtractedLink:
    url: str
    kind: str  # m3u | xtream | epg | unknown
    has_credentials: bool
    masked: str


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _valid(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _candidate(url: str, *, kind: str, has_credentials: bool = False) -> ExtractedLink | None:
    url = url.strip().rstrip(").,;\"'")
    if not _valid(url):
        return None
    return ExtractedLink(url=url, kind=kind, has_credentials=has_credentials, masked=mask_url(url))


def extract_links(text: str, *, limit: int = 50) -> list[ExtractedLink]:
    """Pull IPTV candidates out of free text, in a stable order, without duplicates."""
    found: list[ExtractedLink] = []
    seen: set[str] = set()

    def add(link: ExtractedLink | None) -> None:
        if link is None or link.url in seen or len(found) >= limit:
            return
        seen.add(link.url)
        found.append(link)

    for match in _XTREAM_GET_RE.finditer(text):
        add(_candidate(match.group(0), kind="xtream", has_credentials=True))

    for match in _XTREAM_API_RE.finditer(text):
        add(_candidate(match.group(0), kind="xtream", has_credentials=False))

    for match in _M3U_RE.finditer(text):
        add(_candidate(match.group(0), kind="m3u"))

    for match in _EPG_RE.finditer(text):
        url = match.group(0)
        lowered = url.lower()
        if any(token in lowered for token in ("epg", "guide", "xmltv")):
            add(_candidate(url, kind="epg"))

    # A pasted playlist body: keep only its stream URLs, not the blob itself.
    for block in _EXTM3U_BLOCK_RE.finditer(text):
        for line in block.group(0).splitlines()[: _MAX_PER_BLOCK * 4]:
            stripped = line.strip()
            if stripped.startswith(("http://", "https://")):
                add(_candidate(stripped, kind="m3u"))

    # Base64-embedded lists: decode, then accept only if it really is a playlist.
    for match in _B64_RE.finditer(text[:200_000]):
        if len(found) >= limit:
            break
        try:
            decoded = base64.b64decode(match.group(0) + "===", validate=False)[:200_000]
        except (binascii.Error, ValueError):
            continue
        head = decoded[:16].lstrip(b"\xef\xbb\xbf \r\n\t").upper()
        if not (head.startswith(b"#EXTM3U") or head.startswith(b"HTTP")):
            continue
        for link in extract_links(decoded.decode("utf-8", errors="replace"), limit=limit):
            add(link)

    return found
