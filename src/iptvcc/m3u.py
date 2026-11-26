"""M3U/M3U8 parsing and deterministic playlist rendering.

The parser is deliberately tolerant: real provider playlists contain unquoted attributes,
``#EXTGRP`` instead of ``group-title``, per-entry ``#EXTVLCOPT`` headers and URLs without an
``#EXTINF``. It never raises on a malformed line — it skips it and keeps going.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Final

CONTAINER_TS: Final = "ts"
CONTAINER_HLS: Final = "m3u8"
CONTAINER_DASH: Final = "mpd"
CONTAINER_OTHER: Final = "other"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,6})(?:$|[?#])")

_TS_SUFFIXES = {"ts", "m2ts", "mts"}
_HLS_SUFFIXES = {"m3u8", "m3u"}
_DASH_SUFFIXES = {"mpd"}
_HTTP_SCHEMES = ("http://", "https://")


class PlaylistError(ValueError):
    """Raised when a document cannot be treated as a channel list at all."""


@dataclass
class Entry:
    """One playlist item."""

    url: str
    name: str
    attrs: dict[str, str] = field(default_factory=dict)
    group: str | None = None
    vlc_opts: tuple[str, ...] = ()
    line_no: int = 0

    @property
    def tvg_id(self) -> str | None:
        return _attr(self.attrs, "tvg-id") or None

    @property
    def tvg_name(self) -> str | None:
        return _attr(self.attrs, "tvg-name") or None

    @property
    def logo(self) -> str | None:
        return _attr(self.attrs, "tvg-logo") or _attr(self.attrs, "logo") or None

    @property
    def group_title(self) -> str | None:
        return _attr(self.attrs, "group-title") or self.group or None

    @property
    def container(self) -> str:
        return container_for(self.url)


def _attr(attrs: dict[str, str], name: str) -> str:
    """Case-insensitive attribute lookup (providers are inconsistent about case)."""
    if name in attrs:
        return attrs[name]
    lowered = name.lower()
    for key, value in attrs.items():
        if key.lower() == lowered:
            return value
    return ""


def parse_attributes(payload: str) -> dict[str, str]:
    """Parse ``key="value"`` / ``key='value'`` / ``key=value`` pairs from an ``#EXTINF`` payload.

    A hand-written scanner, not a regular expression. The previous pattern retried the key pattern
    at every position, so an unterminated quote made a single line quadratic: 16 000 characters
    took 5.2 s, and a 200 000-character line would have blocked the event loop for minutes. This
    walks the string once.
    """
    attrs: dict[str, str] = {}
    index = 0
    length = len(payload)
    separators = " \t\r\n,"
    while index < length:
        while index < length and payload[index] in separators:
            index += 1
        start = index
        while index < length and (payload[index].isalnum() or payload[index] in "._-"):
            index += 1
        if index == start:
            # Not a key character: skip it and carry on, which keeps the scan linear.
            index += 1
            continue
        key = payload[start:index]

        while index < length and payload[index].isspace():
            index += 1
        if index >= length or payload[index] != "=":
            continue
        index += 1
        while index < length and payload[index].isspace():
            index += 1

        if index < length and payload[index] in "\"'":
            quote = payload[index]
            end = payload.find(quote, index + 1)
            if end == -1:
                attrs[key] = payload[index + 1 :]
                break
            attrs[key] = payload[index + 1 : end]
            index = end + 1
            continue

        start = index
        while (
            index < length
            and not payload[index].isspace()
            and payload[index] not in ',"\''
        ):
            index += 1
        attrs[key] = payload[start:index]
        if index == start:
            index += 1
    return attrs


def split_extinf(payload: str) -> tuple[str, dict[str, str], str]:
    """Split ``-1 tvg-id="x" group-title="y",Display Name`` into duration, attrs, name.

    The name starts at the first comma that is not inside double quotes, so display names may
    contain commas.
    """
    in_quotes = False
    cut = len(payload)
    for index, char in enumerate(payload):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            cut = index
            break
    head, name = payload[:cut], payload[cut + 1 :] if cut < len(payload) else ""
    duration = ""
    stripped = head.strip()
    if stripped:
        first = stripped.split(None, 1)
        if first and (first[0].lstrip("-").replace(".", "", 1).isdigit()):
            duration = first[0]
            head = stripped[len(first[0]) :]
    return duration, parse_attributes(head), name.strip()


def container_for(url: str) -> str:
    """Best-effort container guess from the URL. Confirmed later by a probe (see health)."""
    match = _EXT_RE.search(url.split("?", 1)[0])
    suffix = match.group(1).lower() if match else ""
    if suffix in _HLS_SUFFIXES:
        return CONTAINER_HLS
    if suffix in _DASH_SUFFIXES:
        return CONTAINER_DASH
    if suffix in _TS_SUFFIXES or suffix == "":
        return CONTAINER_TS
    return CONTAINER_OTHER


def parse_extm3u(line: str) -> dict[str, str]:
    """Parse the ``#EXTM3U`` header attributes (``url-tvg``, ``x-tvg-url``, ...)."""
    return parse_attributes(line[len("#EXTM3U") :])


def iter_entries(lines: Iterable[str]) -> Iterator[Entry]:
    """Yield playlist entries from an iterable of text lines."""
    pending_attrs: dict[str, str] | None = None
    pending_name = ""
    group: str | None = None
    vlc_opts: list[str] = []

    for line_no, raw in enumerate(lines, start=1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue

        if line.upper().startswith("#EXTINF"):
            payload = line.split(":", 1)[1] if ":" in line else ""
            _duration, attrs, name = split_extinf(payload)
            pending_attrs = attrs
            pending_name = name
            vlc_opts = []
            continue

        if line.upper().startswith("#EXTGRP"):
            group = line.split(":", 1)[1].strip() if ":" in line else None
            continue

        if line.upper().startswith("#EXTVLCOPT"):
            option = line.split(":", 1)[1].strip() if ":" in line else ""
            if option:
                vlc_opts.append(option)
            continue

        if line.startswith("#"):
            continue

        entry = Entry(
            url=line,
            name=pending_name or _name_from_url(line),
            attrs=dict(pending_attrs) if pending_attrs is not None else {},
            group=group,
            vlc_opts=tuple(vlc_opts),
            line_no=line_no,
        )
        yield entry
        pending_attrs = None
        pending_name = ""
        group = None
        vlc_opts = []


def validate_playlist(lines: Iterable[str]) -> None:
    """Reject documents that are not a channel list, with a reason an operator can act on.

    An HLS *media* playlist (``#EXT-X-TARGETDURATION``, segments) is the classic wrong paste: it
    would otherwise import every segment as a channel.
    """
    has_entries = False
    for raw in lines:
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXT-X-"):
            raise PlaylistError(
                "this is an HLS media playlist (segments), not a channel list; "
                "use the provider's M3U export instead"
            )
        if not line.startswith("#"):
            has_entries = True
    if not has_entries:
        raise PlaylistError("playlist contains no channel URLs")


def _name_from_url(url: str) -> str:
    tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    return stem.replace("_", " ").replace("-", " ").strip() or "Unnamed"


# --- rendering ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedChannel:
    """Everything the renderer needs for one ``#EXTINF`` line."""

    public_key: str
    display_name: str
    epg_id: str
    logo_url: str | None = None
    number: int | None = None
    group: str | None = None
    ext: str = CONTAINER_TS


def sanitize_name(value: str) -> str:
    """Remove characters that break attribute parsing or player identity.

    OwnTV keys favourites on ``name|group``; Kodi splits ``group-title`` on ``;``;
    a double quote breaks every attribute parser. So: quotes, commas, semicolons and pipes
    are normalised to spaces and control characters are dropped.
    """
    cleaned = _CONTROL_RE.sub(" ", value or "")
    for char in '",;|':
        cleaned = cleaned.replace(char, " ")
    return _WS_RE.sub(" ", cleaned).strip()


def _esc(value: str) -> str:
    return sanitize_name(value)


def render_m3u(
    channels: Iterable[RenderedChannel],
    *,
    base_url: str,
    token: str,
    epg_path: str = "epg.xml",
) -> str:
    """Render a deterministic per-device playlist.

    Determinism is a hard requirement: identical catalog + profile must produce a byte-identical
    file, because players key favourites and channel identity on name/group/tvg-id.
    """
    base = base_url.rstrip("/")
    epg_url = f"{base}/p/{token}/{epg_path}"
    out: list[str] = [f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}" tvg-url="{epg_url}"']
    for channel in channels:
        name = _esc(channel.display_name) or "Unnamed"
        attrs = [
            f'tvg-id="{_esc(channel.epg_id)}"',
            f'tvg-name="{name}"',
        ]
        if channel.logo_url:
            attrs.append(f'tvg-logo="{_esc(channel.logo_url)}"')
        if channel.number is not None:
            attrs.append(f'tvg-chno="{channel.number}"')
        attrs.append(f'channel-id="{channel.public_key}"')
        if channel.group:
            attrs.append(f'group-title="{_esc(channel.group)}"')
        stream_url = f"{base}/p/{token}/s/{channel.public_key}.{channel.ext}"
        out.append(f"#EXTINF:-1 {' '.join(attrs)},{name}")
        out.append(stream_url)
    return "\n".join(out) + "\n"
