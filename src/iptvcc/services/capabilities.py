"""Client capabilities.

A playlist that offers MPEG-TS to an Apple TV is a playlist with dead channels: AVPlayer does not
play raw transport streams. OwnTV (ExoPlayer) is the opposite — it plays TS, and a ``.ts`` URL that
answers with an HLS manifest breaks it.

So the device kind decides which containers may be advertised and which guide size is reasonable.
The registry is deliberately static: capability is a property of the client family, not something an
operator should have to configure per device.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTAINER_TS = "ts"
CONTAINER_HLS = "m3u8"
CONTAINER_DASH = "mpd"

_ALL = frozenset({CONTAINER_TS, CONTAINER_HLS, CONTAINER_DASH})


@dataclass(frozen=True)
class Capabilities:
    containers: frozenset[str]
    max_guide_bytes: int | None = None

    def supports(self, container: str) -> bool:
        return container in self.containers


_REGISTRY: dict[str, Capabilities] = {
    # OwnTV 5.x / ExoPlayer: TS and HLS, no DASH.
    "android_tv": Capabilities(frozenset({CONTAINER_TS, CONTAINER_HLS}), max_guide_bytes=5_000_000),
    # AVPlayer: HLS only.
    "apple_tv": Capabilities(frozenset({CONTAINER_HLS}), max_guide_bytes=8_000_000),
    # VLC, mpv, desktop players: everything.
    "desktop": Capabilities(_ALL),
    # Phone and tablet players are usually HLS-first.
    "mobile": Capabilities(frozenset({CONTAINER_HLS}), max_guide_bytes=4_000_000),
    # Unknown client: the conservative pair every player handles.
    "other": Capabilities(frozenset({CONTAINER_TS, CONTAINER_HLS}), max_guide_bytes=5_000_000),
}


def for_device(kind: str | None) -> Capabilities:
    """Capabilities for a device kind, falling back to the conservative default."""
    return _REGISTRY.get((kind or "other").strip().lower(), _REGISTRY["other"])


def known_kinds() -> list[str]:
    return sorted(_REGISTRY)


def effective_guide_cap(kind: str | None, configured: int) -> int:
    """The smaller of the operator's cap and what the client family tolerates; 0 means no cap."""
    capability = for_device(kind).max_guide_bytes
    if configured <= 0:
        return capability or 0
    if capability is None:
        return configured
    return min(configured, capability)
