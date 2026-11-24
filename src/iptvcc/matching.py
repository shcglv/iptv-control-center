"""Channel normalisation and matching.

Ladder (highest confidence first):
  1. exact ``tvg-id``          -> 1.00
  2. exact normalised name     -> accepted
  3. fuzzy ratio (rapidfuzz)   -> accepted above ``FUZZY_ACCEPT``, reviewed between the thresholds

Everything below the review threshold creates a new channel instead of merging: silently
merging two different channels is far worse than showing a duplicate.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from rapidfuzz import fuzz

FUZZY_ACCEPT: Final = 92.0
FUZZY_REVIEW: Final = 78.0
REGION_BONUS: Final = 15.0

_STOPWORDS: Final = frozenset(
    {
        "tv",
        "channel",
        "network",
        "television",
        "hd",
        "fhd",
        "uhd",
        "4k",
        "8k",
        "1080p",
        "720p",
        "540p",
        "480p",
        "24/7",
        "plus",
        "east",
        "west",
        "backup",
    }
)

_BRACKETS_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_REGION_RE = re.compile(r"(?:\.|@)([a-z]{2})\b", re.IGNORECASE)


def normalize(name: str) -> str:
    """Normalise a channel name into a comparable key."""
    value = unicodedata.normalize("NFKC", name or "").lower()
    value = value.replace("&", " and ")
    value = _BRACKETS_RE.sub(" ", value)
    value = _PUNCT_RE.sub(" ", value)
    tokens = [token for token in value.split() if token not in _STOPWORDS]
    return _WS_RE.sub(" ", " ".join(tokens)).strip()


def region_of(*values: str | None) -> str | None:
    """Extract an ISO-3166 alpha-2 region from ``tvg-id``-like strings (``bbc1.uk``)."""
    for value in values:
        if not value:
            continue
        match = _REGION_RE.search(value)
        if match:
            return match.group(1).lower()
    return None


def score(
    left: str, right: str, *, left_region: str | None = None, right_region: str | None = None
) -> float:
    """Similarity score in ``0..100`` with a regional tie-break bonus."""
    if not left or not right:
        return 0.0
    if left == right:
        return 100.0
    base = float(fuzz.ratio(left, right))
    if left_region and right_region and left_region == right_region:
        base = min(100.0, base + REGION_BONUS / 3)
    elif left_region and right_region:
        base = max(0.0, base - REGION_BONUS / 3)
    return base


def is_auto_match(value: float) -> bool:
    return value >= FUZZY_ACCEPT


def needs_review(value: float) -> bool:
    return FUZZY_REVIEW <= value < FUZZY_ACCEPT
