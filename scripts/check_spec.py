"""Check the product against the invariants agreed in the specification.

Behaviour is covered by the test suites; this checks the numbers and shapes that the approved design
fixed, because those are exactly what drifts silently when code is refactored:

* the quality formula's weights and their sum;
* anti-flapping: a switch needs +10 points and a 15-minute dwell;
* the public rate limit, session lifetime and its hash;
* the fuzzy-match bands (92 accept / 78 review);
* direct streaming: the resolver answers with a redirect and nothing streams media;
* the playlist advertises its guide through all three historical attribute names.

    .\\.venv\\Scripts\\python.exe scripts\\check_spec.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8", errors="replace")


def main() -> int:
    from iptvcc import auth, quality
    from iptvcc.config import Settings
    from iptvcc.matching import FUZZY_ACCEPT, FUZZY_REVIEW

    weights = {
        "reliability": quality.W_RELIABILITY,
        "startup": quality.W_STARTUP,
        "technical": quality.W_TECHNICAL,
        "compatibility": quality.W_COMPATIBILITY,
        "errors": quality.W_ERRORS,
    }
    expected = {
        "reliability": 0.40,
        "startup": 0.15,
        "technical": 0.25,
        "compatibility": 0.10,
        "errors": 0.10,
    }
    for name, value in expected.items():
        if abs(weights[name] - value) > 1e-9:
            fail(f"quality weight {name} is {weights[name]}, spec says {value}")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        fail(f"quality weights sum to {total}, expected 1.0")

    if quality.SWITCH_MARGIN != 10.0:
        fail(f"switch margin is {quality.SWITCH_MARGIN}, spec says 10.0")
    if quality.DWELL_SECONDS != 15 * 60:
        fail(f"dwell is {quality.DWELL_SECONDS}s, spec says 900s")

    settings = Settings()
    if settings.public_rate_limit_per_minute != 120:
        fail(f"public rate limit is {settings.public_rate_limit_per_minute}, spec says 120")
    if auth.SESSION_TTL.total_seconds() != 12 * 3600:
        fail(f"session lifetime is {auth.SESSION_TTL}, spec says 12 hours")
    if "sha256" not in read("src/iptvcc/auth.py"):
        fail("session tokens are not hashed with sha256 at rest")

    if (FUZZY_ACCEPT, FUZZY_REVIEW) != (92.0, 78.0):
        fail(f"fuzzy bands are {FUZZY_ACCEPT}/{FUZZY_REVIEW}, spec says 92/78")

    if settings.probe_host_concurrency < 1:
        fail("probe_host_concurrency must be at least 1")
    if settings.epg_max_bytes <= 0:
        fail("epg_max_bytes must be positive")

    api = read("src/iptvcc/api.py")
    if "StreamingResponse" in api:
        fail("api.py imports StreamingResponse: the server must not carry media")
    if api.count("RedirectResponse") < 1:
        fail("the resolver no longer returns a redirect")
    resolve_block = re.search(r"async def resolve\(.*?(?=\n@app\.|\Z)", api, re.S)
    if resolve_block is None:
        fail("the resolver handler was not found")
    elif "RedirectResponse" not in resolve_block.group(0):
        fail("the resolver handler does not return RedirectResponse")

    m3u = read("src/iptvcc/m3u.py")
    for attribute in ("url-tvg", "x-tvg-url", "tvg-url"):
        if attribute not in m3u:
            fail(f"the playlist no longer emits {attribute}")

    if FAILURES:
        print(f"{len(FAILURES)} specification drift(s):")
        for problem in FAILURES:
            print(f"  - {problem}")
        return 1
    print("RESULT: SPECIFICATION INVARIANTS HOLD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
