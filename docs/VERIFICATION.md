# Verification report

**Date:** 2026-11-26
**Version:** 1.4.0 (tagged `v1.4.0`)
**Environment:** Windows development machine, Python 3.12.0, Docker 29.9.3, Chromium via Playwright
**Database under test:** SQLite **and PostgreSQL 16** — the full suite passes on both

Every command below was run for this report; nothing is copied from earlier rounds. What is *not*
verified is listed at the end, and mapped against the specification in [`STATUS.md`](STATUS.md).

## Summary

| Area | Command | Result |
|---|---|---|
| Backend (SQLite) | `pytest -q` | **251 passed**, 13 skipped |
| Backend (PostgreSQL 16) | `IPTV_TEST_DATABASE_URL=postgresql+psycopg://… pytest -q` | **251 passed**, 13 skipped |
| Backup (PostgreSQL) | `scripts/verify_pg_container.py` | **PASSED** — migrations at start (`ec324056e4de`), import, 302 resolver, restart persistence, 78 KB dump with 28 table entries |
| Lint | `ruff check src tests scripts` | clean |
| Types | `mypy src` | clean, 41 files |
| Browser | `pytest tests/test_browser.py` (`IPTV_BROWSER_TESTS=1`) | **7 passed** |
| Container | `pytest tests/test_container.py` (`IPTV_CONTAINER_TESTS=1`) | **3 passed**, image rebuilt from HEAD |
| Real public data | `pytest tests/test_real_world.py` (`IPTV_NETWORK_TESTS=1`) | **3 passed** — 1 059-entry public playlist imported to 1 028 channels; 7/12 real streams reachable; 3 channels resolved to real HLS URLs (`HEAD` 200). Details: [`REAL-WORLD.md`](REAL-WORLD.md) |
| Real EPG + active probe | `data/live_real_epg.py`, ffprobe in the ffmpeg image | 20.9 MiB public guide → 287 channels / 48 814 programmes; ffprobe read a real HLS stream (1280×720 h264) and correctly failed a zero-stream manifest |
| Real decoder through the 302 | `scripts/verify_decoder.py` | **PASSED** — ffmpeg in a container followed our resolver's 302 to a public HLS stream and decoded 3 s (exit 0, no errors) |
| Restore (SQLite) | `data/live_restore_drill.py` | **PASSED** |
| Preflight | `scripts/preflight.py` | 8/8 with a valid key; fails correctly without one |
| Performance | `scripts/bench_scale.py --channels 3000 ...` | import 1.78 s, render 0.34 s, EPG 12.4 s, guide 4.5 s |

### A PostgreSQL-only defect found by this sweep

The duplicate-merge path passed all 251 SQLite tests and failed three tests on PostgreSQL with
`StaleDataError`. Cause: deleting the duplicate channel cascaded to the `match_suggestion` row that
pointed at it, so the follow-up `UPDATE … status='accepted'` matched zero rows. SQLite does not
enforce foreign keys by default, so the cascade never happened there. The merge now removes the
resolved suggestion explicitly, before the channel, and checks the decision against the database
rather than a possibly-stale object. Repeat-accept is a `404` (the suggestion is gone), not a `409`.

## Backend suite

```
251 passed, 13 skipped
```

Coverage by area: M3U parsing and idempotent import (plus a 17-case provider corpus), matching,
quality scoring and anti-flapping, scheduler lease, EPG ingest and mapping (plus a 19-case XMLTV
corpus, and the smart-TV guide cap), delivery and the 302 resolver, client capabilities per device
family, profiles and drag ordering, category visibility, duplicate detection and merge, discovery
(extractors, adapters, review), Telegram (adapter, commands, notifications), auth and CSRF, SSRF
rules, rate limiting, backups, catalog and blocks, preflight, diagnostics and request ids, active
probe mode.

The 13 skips are the seven browser tests, three container tests and three network tests, which are
opt-in.

## Browser suite

```
7 passed
```

Against a real server process and Chromium: login with a live TOTP and navigation across every
section; profile edit and hide; drag-and-drop reorder plus the arrow fallback; catalog import,
matching and a block; discovery review against a local feed with credential masking; and the
"Почему?" diagnostics panel.

## Container acceptance

```
3 passed in 246.27s
```

Image rebuilt from the current commit. Over HTTP: admin created through the container CLI, playlist
import, a profile hiding CNN, a device playlist with two channels and all three EPG attributes, a 302
resolver pointing at the provider, XMLTV import and per-device guide, discovery against a feed on the
host, accepting the candidate, then `docker restart` and verification that the data survived. It also
asserts `/healthz`, the SPA at `/`, and UID 10001.

Images: default **349 MB**; with `--build-arg INSTALL_FFMPEG=true` **977 MB** (ffprobe 7.1.5 verified
inside, UID still 10001).

## Preflight

With a generated key all eight checks pass (`encryption key`, `public base URL`, `display timezone`,
`telegram`, `rate limit`, `artifacts dir`, `database`, `migrations`). Without a key it reports
`IPTV_ENCRYPTION_KEY is not set` and exits 1; with a malformed key it reports
`decodes to 1 bytes, expected 32`. The check is meant to fail.

## What this report does **not** prove

| Gap | Why it matters | Tracked in |
|---|---|---|
| A **real provider** has never been imported | Fixtures cannot reproduce a provider's malformed EXTM3U, geo rules or expiring credentials | `ACCEPTANCE.md` |
| **OwnTV / VLC** on a physical TV | Player behaviour with real containers is untested | `ACCEPTANCE.md` |
| A **real Telegram bot** | Only a fake Bot API and a live local run were used | `ACCEPTANCE.md` |
| Active probe against a **real stream** | ffprobe invocation is tested with a fake binary only | `STATUS.md` |
| The **target server** (2 vCPU, Traefik, LE) | No deployment has happened | `DEPLOYMENT.md` |
| **CI has never run** | The workflow is committed but the repository has no remote | `STATUS.md` |

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src

$env:IPTV_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_browser.py -q
$env:IPTV_CONTAINER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_container.py -q

.\.venv\Scripts\python.exe data\live_restore_drill.py
.\.venv\Scripts\python.exe scripts\bench_scale.py --channels 3000 --sources 2 --epg-channels 3000 --programmes 24
```
