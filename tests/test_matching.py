"""Normalisation and matching tests."""

from __future__ import annotations

from iptvcc.matching import (
    FUZZY_ACCEPT,
    is_auto_match,
    needs_review,
    normalize,
    region_of,
    score,
)


def test_normalize_strips_quality_and_punctuation() -> None:
    assert normalize("BBC One HD") == "bbc one"
    assert normalize("Eurosport 1 [HD]") == "eurosport 1"
    assert normalize("CNN (US)") == "cnn"
    assert normalize("Channel 4 +1") == "4 1"
    assert normalize("  ARD   Das Erste  ") == "ard das erste"


def test_normalize_handles_ampersand() -> None:
    assert normalize("Rock & Roll TV") == "rock and roll"


def test_region_extraction() -> None:
    assert region_of("bbc1.uk") == "uk"
    assert region_of("cnn.us@HD") == "us"
    assert region_of("no-region-here") is None


def test_score_exact_and_fuzzy() -> None:
    assert score("bbc one", "bbc one") == 100.0
    assert score("bbc one", "bbc 1") < FUZZY_ACCEPT
    assert score("eurosport 1", "eurosport 1 ") >= 0.0
    assert score("", "x") == 0.0


def test_region_bonus_prefers_same_region() -> None:
    same = score("bbc one", "bbc one hd", left_region="uk", right_region="uk")
    other = score("bbc one", "bbc one hd", left_region="uk", right_region="us")
    assert same >= other


def test_thresholds() -> None:
    assert is_auto_match(95.0)
    assert not is_auto_match(90.0)
    assert needs_review(85.0)
    assert not needs_review(70.0)
