# Verification report

**Date:** 2026-11-25
**Version:** 0.3.0
**Commit:** `1b7b22d` plus the browser-test fix in this change
**Environment:** Windows development machine, Python 3.12.0, Docker 29.9.3, Chromium via Playwright
**Database under test:** SQLite (tests) — PostgreSQL has not been exercised

This report is the evidence behind "it works". Every command below was run for this report; nothing
is copied from earlier rounds.

## Summary

| Area | Command | Result |
|---|---|---|
| Backend | `pytest -q` | **178 passed**, 7 skipped |
| Lint | `ruff check src tests scripts` | clean |
| Types | `mypy src` | clean, 38 files |
| Browser | `pytest tests/test_browser.py` (`IPTV_BROWSER_TESTS=1`) | **4 passed** |
| Container | `pytest tests/test_container.py` (`IPTV_CONTAINER_TESTS=1`) | **3 passed**, image rebuilt from HEAD |
| Restore | `data/live_restore_drill.py` | **PASSED** |
| Preflight | `scripts/preflight.py` | 8/8 checks pass with a valid key; fails correctly without one |
| Performance | `scripts/bench_scale.py --channels 3000 ...` | import 1.78 s, render 0.34 s, EPG 12.4 s, guide 4.5 s |

## Backend suite

```
178 passed, 7 skipped in 169.59s
```

The 7 skips are the four browser tests and three container tests, which are opt-in. Coverage
includes: M3U parsing and idempotent import, matching, quality scoring and anti-flapping, the
scheduler lease, EPG ingest and mapping, delivery and the 302 resolver, profiles, discovery
(extractors, adapters, review), Telegram (adapter, commands, notifications), auth and CSRF, SSRF
rules, rate limiting, backups, the catalog and blocklist, and preflight.

## Browser suite

```
4 passed in 131.09s
```

Against a real server process and Chromium:

1. login with a live TOTP, walk every section, assert no page errors;
2. create a profile, hide a channel, see `изменён` and `2 / 3`;
3. import a catalog, match, add a block, delete it;
4. run discovery against a local feed, confirm credentials are masked, accept the candidate and find
   the source.

**A race was found and fixed during this sweep:** the discovery test asserted on the Sources table
immediately after navigation, which failed once the machine was busy building the Docker image. It
now waits for the accepted source cell instead of counting it at one instant.

## Container acceptance

```
3 passed in 239.61s
```

The image was rebuilt from the current commit (no layer reuse of old source) and exercised over HTTP:
admin created through the container CLI, playlist import, a profile hiding CNN, a device playlist
with two channels and all three EPG attributes, a 302 resolver pointing at the provider, XMLTV import
and per-device guide, discovery against a feed on the host, accepting the candidate, then
`docker restart` and verification that the data survived. It also asserts `/healthz`, the SPA at `/`,
and that the process runs as UID 10001.

Image: `iptv-control-center:acceptance`, **349 MB**, id `880d2bed72dc`.

## Restore drill

```
seeded: {'parsed': 3, 'created': 3, ..., 'channels_created': 3}
channels before backup: 3
backup: ...\backup.db 294912 bytes
rows: {'admin_user': 1, 'channel': 3, 'stream': 3}
live database replaced from backup
channels after restore: 3
sources after restore: [('Drill', 3)]
RESULT: RESTORE DRILL PASSED
```

## Preflight

With a generated key:

```
[+] encryption key    32 bytes, unique
[+] public base URL   https://iptv.example
[+] display timezone  Europe/Moscow
[+] telegram          disabled (no token)
[+] rate limit        120/min
[+] artifacts dir     data\preflight-artifacts
[+] database          sqlite+aiosqlite:///./data/mig8.db
[+] migrations        198a8944b683
0 failed, 0 warnings, 8 checks
```

Without a key: `[x] encryption key  IPTV_ENCRYPTION_KEY is not set`, `1 failed`. With a malformed
key: `[x] encryption key  decodes to 1 bytes, expected 32`. The check is meant to fail.

## Performance (3000 channels, 6000 streams, 72 000 programmes)

| Operation | Time |
|---|---|
| Import (cold) | 1.78 s |
| Import (re-run) | 0.65 s |
| Playlist render | 0.34 s (572 KiB) |
| EPG ingest | 12.39 s |
| EPG mapping | 0.32 s |
| Guide build | 4.53 s (11.7 MiB XML, 0.4 MiB gzip) |

10 000-channel numbers and methodology are in [`PERFORMANCE.md`](PERFORMANCE.md). These figures are
SQLite on a development machine; PostgreSQL on the target server is unmeasured.

## What this report does **not** prove

| Gap | Why it matters |
|---|---|
| A **real provider** has never been imported | Fixtures cannot reproduce a provider's malformed EXTM3U, geo rules or expiring credentials |
| **OwnTV / VLC** on a physical TV | Container and player behaviour with real containers is untested |
| A **real Telegram bot** | Only a fake Bot API has been used; token shape is checked, delivery is not |
| **PostgreSQL** | All tests run on SQLite; `pg_dump` path never executed |
| The **target server** (2 vCPU, Traefik, LE) | No deployment has happened |
| **CI has never run** | The workflow is committed but the repository has no remote |

Closing the first three is what [`ACCEPTANCE.md`](ACCEPTANCE.md) is for; closing the last two needs
the repository remote and the Coolify GO.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m mypy src

$env:IPTV_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_browser.py -q
$env:IPTV_CONTAINER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_container.py -q

.\.venv\Scripts\python.exe data\live_restore_drill.py
.\.venv\Scripts\python.exe scripts\bench_scale.py --channels 3000 --sources 2 --epg-channels 3000 --programmes 24
```
