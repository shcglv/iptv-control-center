# Verification report

**Date:** 2026-11-26
**Version:** 1.6.6 (tagged `v1.6.6`)
**Environment:** Windows development machine, Python 3.12.0, Docker 29.9.3, Chromium via Playwright
**Database under test:** SQLite **and PostgreSQL 16** — the full suite passes on both

Every command below was run for this report; nothing is copied from earlier rounds. What is *not*
verified is listed at the end, and mapped against the specification in [`STATUS.md`](STATUS.md).

## Summary

| Area | Command | Result |
|---|---|---|
| Backend (SQLite) | `pytest -q` | **271 passed**, 16 skipped |
| Backend (PostgreSQL 16) | `IPTV_TEST_DATABASE_URL=postgresql+psycopg://… pytest -q` | **271 passed**, 16 skipped |
| Backup (PostgreSQL) | `scripts/verify_pg_container.py` | **PASSED** — migrations at start (`ec324056e4de`), import, 302 resolver, restart persistence, 78 KB dump with 28 table entries |
| Restore (PostgreSQL) | `scripts/verify_restore.py` | **PASSED** — dump restored into a fresh database, row counts match across 28 tables (12 rows), and the application served 2 channels from the restored copy |
| Upgrade (PostgreSQL) | `scripts/verify_upgrade.py` | **PASSED** — schema at `198a8944b683`, data written through the real import path, upgraded to `head`, rows intact, device playlist still served |
| Scheduler loop | `scripts/verify_scheduler.py` | **PASSED** — the real application with a 2 s tick recorded 5 ticks in 8 s, with state persisted and no errors logged |
| Lint | `ruff check src tests scripts` | clean |
| Types | `mypy src` | clean, 41 files |
| Browser | `pytest tests/test_browser.py` (`IPTV_BROWSER_TESTS=1`) | **8 passed** — including the panel at 390×844 with zero horizontal overflow |
| Container | `pytest tests/test_container.py` (`IPTV_CONTAINER_TESTS=1`) | **3 passed**, image rebuilt from 1.6.6 |
| Real discovery | `pytest tests/test_discovery_live.py` (`IPTV_NETWORK_TESTS=1`) | **2 passed** — a real GitHub page yields an `m3u` link, a real Atom feed parses and correctly yields none |
| Real public data | `pytest tests/test_real_world.py` (`IPTV_NETWORK_TESTS=1`) | **3 passed** — 1 059-entry public playlist imported to 1 028 channels; 7/12 real streams reachable; 3 channels resolved to real HLS URLs (`HEAD` 200). Details: [`REAL-WORLD.md`](REAL-WORLD.md) |
| Real EPG + active probe | `data/live_real_epg.py`, ffprobe in the ffmpeg image | 20.9 MiB public guide → 287 channels / 48 814 programmes; ffprobe read a real HLS stream (1280×720 h264) and correctly failed a zero-stream manifest |
| Real decoder through the 302 | `scripts/verify_decoder.py` | **PASSED** — ffmpeg in a container followed our resolver's 302 to a public HLS stream and decoded 3 s (exit 0, no errors) |
| Panel checks in the container | `scripts/verify_panel.py` | **PASSED** — 8/8 `ok` in a production-shaped container (`app_env=production`, HTTPS base URL), migrations `ec324056e4de`, version 1.6.6 |
| Restore (SQLite) | `data/live_restore_drill.py` | **PASSED** |
| Preflight | `scripts/preflight.py` | 8/8 with a valid key; fails correctly without one |
| Performance | `scripts/bench_scale.py --channels 3000 --sources 10 --epg-channels 100 --programmes 2400` | import 5.58 s (30 000 streams), re-run 3.17 s, render 0.82 s, EPG 37.67 s (240 000 programmes), guide 2.01 s |

### Two test-side defects found during this sweep

Both were in the verification harness rather than the product, and both are fixed:

- `test_system_check_fails_on_an_unmigrated_database` assumed SQLite's schema (`create_all`, no
  `alembic_version`). The PostgreSQL harness runs the real migrations, so the check was legitimately
  `ok` and the test failed there. It now removes the recorded revision itself, asserts the failure,
  and restores it, which is deterministic on both databases.
- `scripts/verify_pg_container.py` assumed its Docker network still existed and failed with
  `network iptv-test not found` after a cleanup. It now creates the network and connects the
  database container itself.

One browser run failed while Docker Desktop was starting (378 s for the suite, versus 88 s when
idle); the failing test passed alone and the full suite passed on an idle machine. Recorded because
timing flakes and real failures look identical in a log.

### A PostgreSQL-only defect found by this sweep

The duplicate-merge path passed all 251 SQLite tests and failed three tests on PostgreSQL with
`StaleDataError`. Cause: deleting the duplicate channel cascaded to the `match_suggestion` row that
pointed at it, so the follow-up `UPDATE … status='accepted'` matched zero rows. SQLite does not
enforce foreign keys by default, so the cascade never happened there. The merge now removes the
resolved suggestion explicitly, before the channel, and checks the decision against the database
rather than a possibly-stale object. Repeat-accept is a `404` (the suggestion is gone), not a `409`.

## Backend suite

```
271 passed, 16 skipped
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
