"""Quality scoring.

The composite score deliberately does **not** let resolution dominate: a stable 720p stream is
worth more than a flapping 4K one. Weights mirror the approved design:

    Balancer = 0.40 * reliability + 0.15 * startup + 0.25 * technical
             + 0.10 * compatibility + 0.10 * error_health

``technical`` here is 0.55 * resolution + 0.30 * codec + 0.15 * bitrate, because FPS is not probed
by the light probe (it needs ffprobe, which belongs to the optional active mode).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

W_RELIABILITY = 0.40
W_STARTUP = 0.15
W_TECHNICAL = 0.25
W_COMPATIBILITY = 0.10
W_ERRORS = 0.10

# Hysteresis: switch only when the alternative is clearly better, and never within the dwell time.
SWITCH_MARGIN = 10.0
DWELL_SECONDS = 15 * 60

_RESOLUTION_SCORES: dict[str, float] = {
    "3840x2160": 1.00,
    "1920x1080": 1.00,
    "1280x720": 0.75,
    "960x540": 0.55,
    "854x480": 0.40,
}

_CODEC_SCORES: dict[str, float] = {
    "avc1": 1.00,
    "h264": 1.00,
    "hvc1": 0.90,
    "hev1": 0.90,
    "hevc": 0.90,
    "av01": 0.80,
}


def resolution_score(resolution: str | None) -> float:
    if not resolution:
        return 0.5
    if resolution in _RESOLUTION_SCORES:
        return _RESOLUTION_SCORES[resolution]
    try:
        width, height = (int(part) for part in resolution.lower().split("x", 1))
    except (ValueError, TypeError):
        return 0.5
    pixels = width * height
    if pixels >= 1920 * 1080:
        return 1.0
    if pixels >= 1280 * 720:
        return 0.75
    if pixels >= 854 * 480:
        return 0.45
    return 0.3


def codec_score(codec: str | None) -> float:
    if not codec:
        return 0.5
    base = codec.split(".", 1)[0].lower()
    return _CODEC_SCORES.get(base, 0.7)


def bitrate_score(bandwidth_bps: int | None, resolution: str | None) -> float:
    """Reward bitrate that is adequate for the resolution, not bitrate for its own sake."""
    if not bandwidth_bps:
        return 0.5
    height = 0
    if resolution and "x" in resolution:
        try:
            height = int(resolution.lower().split("x", 1)[1])
        except ValueError:
            height = 0
    target = {2160: 15_000_000, 1080: 4_000_000, 720: 2_000_000}.get(height, 1_500_000)
    ratio = bandwidth_bps / target
    if ratio >= 1.0:
        return 1.0
    return max(0.2, ratio)


def reliability_score(uptime_ratio: float | None, checks: int) -> float:
    if not checks:
        return 0.5  # unknown: neither rewarded nor punished
    return max(0.0, min(1.0, uptime_ratio or 0.0))


def startup_score(p90_ttfb_ms: float | None) -> float:
    if p90_ttfb_ms is None:
        return 0.5
    return max(0.0, 1.0 - min(p90_ttfb_ms, 5000.0) / 5000.0)


def errors_score(failures: int, checks: int) -> float:
    if not checks:
        return 0.5
    return math.exp(-max(0, failures) / 5.0)


def technical_score(
    resolution: str | None, video_codec: str | None, bandwidth_bps: int | None
) -> float:
    return (
        0.55 * resolution_score(resolution)
        + 0.30 * codec_score(video_codec)
        + 0.15 * bitrate_score(bandwidth_bps, resolution)
    )


def compatibility_score(container: str, requested_ext: str) -> float:
    """The selector already filters by container; this scores residual compatibility risk."""
    return 1.0 if container == requested_ext else 0.0


@dataclass(frozen=True)
class ScoreBreakdown:
    reliability: float
    startup: float
    technical: float
    compatibility: float
    errors: float
    total: float


def compute(
    *,
    uptime_ratio: float | None,
    checks: int,
    failures: int,
    p90_ttfb_ms: float | None,
    resolution: str | None,
    video_codec: str | None,
    bandwidth_bps: int | None,
    container: str,
    requested_ext: str,
) -> ScoreBreakdown:
    reliability = reliability_score(uptime_ratio, checks)
    startup = startup_score(p90_ttfb_ms)
    technical = technical_score(resolution, video_codec, bandwidth_bps)
    compatibility = compatibility_score(container, requested_ext)
    errors = errors_score(failures, checks)
    total = (
        W_RELIABILITY * reliability
        + W_STARTUP * startup
        + W_TECHNICAL * technical
        + W_COMPATIBILITY * compatibility
        + W_ERRORS * errors
    ) * 100.0
    return ScoreBreakdown(
        reliability=round(reliability, 4),
        startup=round(startup, 4),
        technical=round(technical, 4),
        compatibility=round(compatibility, 4),
        errors=round(errors, 4),
        total=round(total, 2),
    )


def should_switch(current_score: float, best_score: float, *, age_seconds: int) -> bool:
    """Anti-flapping rule: require a clear margin and a minimum dwell time."""
    if best_score < current_score + SWITCH_MARGIN:
        return False
    return age_seconds >= DWELL_SECONDS or current_score <= 0.0
