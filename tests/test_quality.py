"""Quality scoring: weights, monotonicity and the anti-flapping rule."""

from __future__ import annotations

from iptvcc.quality import (
    DWELL_SECONDS,
    SWITCH_MARGIN,
    codec_score,
    compute,
    errors_score,
    reliability_score,
    resolution_score,
    should_switch,
    startup_score,
    technical_score,
)


def test_resolution_score_ordering() -> None:
    assert resolution_score("1920x1080") > resolution_score("1280x720")
    assert resolution_score("1280x720") > resolution_score("854x480")
    assert resolution_score(None) == 0.5
    assert resolution_score("bogus") == 0.5


def test_codec_score_ordering() -> None:
    assert codec_score("avc1.640028") == 1.0
    assert codec_score("hvc1.1.6") == 0.9
    assert codec_score(None) == 0.5


def test_reliability_and_startup_bounds() -> None:
    assert reliability_score(1.0, 10) == 1.0
    assert reliability_score(0.0, 10) == 0.0
    assert reliability_score(None, 0) == 0.5
    assert startup_score(0) == 1.0
    assert startup_score(5000) == 0.0
    assert startup_score(10_000) == 0.0
    assert startup_score(None) == 0.5


def test_errors_score_decays() -> None:
    assert errors_score(0, 10) == 1.0
    assert errors_score(5, 10) < errors_score(0, 10)
    assert errors_score(50, 60) < 0.01


def test_technical_score_uses_more_than_resolution() -> None:
    good = technical_score("1920x1080", "avc1", 5_000_000)
    poor_codec = technical_score("1920x1080", "av01", 5_000_000)
    assert good > poor_codec


def test_stable_720p_beats_flapping_4k() -> None:
    """The whole point of Balanced: reliability outranks raw resolution."""
    stable_720 = compute(
        uptime_ratio=1.0,
        checks=20,
        failures=0,
        p90_ttfb_ms=400,
        resolution="1280x720",
        video_codec="avc1",
        bandwidth_bps=3_000_000,
        container="ts",
        requested_ext="ts",
    )
    flaky_4k = compute(
        uptime_ratio=0.45,
        checks=20,
        failures=11,
        p90_ttfb_ms=3500,
        resolution="3840x2160",
        video_codec="hvc1",
        bandwidth_bps=12_000_000,
        container="ts",
        requested_ext="ts",
    )
    assert stable_720.total > flaky_4k.total


def test_container_mismatch_zeroes_compatibility() -> None:
    breakdown = compute(
        uptime_ratio=1.0,
        checks=5,
        failures=0,
        p90_ttfb_ms=200,
        resolution="1920x1080",
        video_codec="avc1",
        bandwidth_bps=5_000_000,
        container="m3u8",
        requested_ext="ts",
    )
    assert breakdown.compatibility == 0.0


def test_switch_requires_margin_and_dwell() -> None:
    # Small improvement: never switch.
    assert not should_switch(80.0, 80.0 + SWITCH_MARGIN - 1, age_seconds=DWELL_SECONDS * 2)
    # Clear improvement but too recent: keep.
    assert not should_switch(60.0, 90.0, age_seconds=60)
    # Clear improvement and dwell elapsed: switch.
    assert should_switch(60.0, 90.0, age_seconds=DWELL_SECONDS)
    # Current source is dead: switch immediately.
    assert should_switch(0.0, 40.0, age_seconds=0)
