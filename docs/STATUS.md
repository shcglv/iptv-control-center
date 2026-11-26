# Status against the specification

**Version 1.7.4 · 2026-11-26 · tagged `v1.7.4`**

What exists, what does not, and how each claim is verified. "Verified" means a test or a recorded
run proves it; "implemented" means the code exists but no test or run has exercised it end to end.
Release notes and install steps: [`RELEASE.md`](../RELEASE.md).

## Core product

| Requirement | Status | Evidence |
|---|---|---|
| Real decoder through the 302 | ✅ | `scripts/verify_decoder.py`: ffmpeg in a container fetched our resolver URL, followed the 302 and decoded 3 s with exit 0; VLC could not be automated headlessly (`REAL-WORLD.md`) |
| **Direct streaming** — the server never carries video | ✅ | 302 resolver returns `Location: <provider URL>`; container test asserts the redirect target; **3 channels resolved to real public HLS endpoints, `HEAD` 200** (`REAL-WORLD.md`) |
| Pure-Direct alternative (playlist emits provider URLs) | ❌ not implemented | Profile flag exists in the plan, not in code |
| M3U import, tolerant parser, idempotent re-import | ✅ | 17-test provider corpus + import tests; **real public playlist**: 1 059 entries → 1 028 channels in 0.6 s (`REAL-WORLD.md`) |
| Stale marking instead of deletion | ✅ | `test_api.py` |
| Channel catalog, permanent identity, public keys | ✅ | model + tests |
| Matching: exact `tvg-id`, normalised name, fuzzy thresholds | ✅ | `test_matching.py` |
| Fuzzy **review queue** (78–92 %) | ✅ | scan by shared first word + score band, human accept/reject; accepting merges links, overrides and EPG mapping; `test_suggestions.py` + Catalog card |
| Manual merge / split / field overrides | ✅ merge via the review queue; ❌ split and per-field overrides | — |
| Playlist profiles: visibility, order, category rules, source pins | ✅ | `test_profiles.py`, drag browser test |
| Drag-and-drop ordering with keyboard fallback | ✅ | browser test |
| Category visibility editor, impact panel, in-editor preview | ✅ categories (visibility + counts); ❌ impact panel and preview | profile screen; `test_profiles.py`, browser test |
| Devices: hashed token, instant rotation/revocation | ✅ | `test_api.py` |
| Deterministic per-device playlist, all three EPG attributes | ✅ | container test |
| **Client Compatibility Engine** (`RenderPlan`) | ✅ | per-family container sets and guide caps: a TS-only channel is absent from an Apple TV playlist and its `.ts` resolver request is refused; `test_capabilities.py` |
| XMLTV ingest: streaming, timezone offsets, retention, replace | ✅ | 19-test EPG corpus; **real public guide**: 287 channels, 48 814 programmes from a 20.9 MiB file (`REAL-WORLD.md`) |
| Channel↔EPG mapping, mapped ids emitted in the playlist | ✅ | `test_epg.py`, container test |
| Per-device guide, cached per profile | ✅ | container test, `test_profiles.py` |
| Smart-TV guide size cap | ✅ | renders 7→3→1-day windows until the document fits `IPTV_EPG_MAX_BYTES` (5 MB default), never truncating XML; `test_epg_cap.py` |
| Health probes, aggregates, composite score | ✅ | `test_health.py` |
| Anti-flapping (+10 margin, 15-min dwell) | ✅ | `test_quality.py`, `test_scheduler.py` |
| Autonomous scheduler with DB lease and watchdog | ✅ | `test_scheduler.py` |
| Active probe mode (ffprobe) | ✅ | `test_active_probe.py` plus a **real stream**: 1280×720 h264 from a public HLS URL; a manifest with zero streams now fails instead of passing (`REAL-WORLD.md`) |
| Provider connection-slot budget | ✅ per-host probe concurrency (IPTV_PROBE_HOST_CONCURRENCY, default 2) | test_health.py::test_probe_round_caps_concurrency_per_host |
| Source Discovery: extractors, RSS/HTML, candidate review | ✅ | `test_discovery.py`, browser test |
| Channel identity catalog + take-down blocks | ✅ | `test_catalog.py`, browser test |
| Authenticated forum sessions, scheduled re-crawls | ❌ not implemented | — |
| Telegram bot: commands, alerts, dedupe, recovery | ✅ | `test_telegram.py` + live run against a fake Bot API |
| Rich messages / widgets / topics, daily scheduled report | ❌ not implemented | — |
| Web UI | ✅ 9 screens | 6 browser tests |

## Platform

| Requirement | Status | Evidence |
|---|---|---|
| FastAPI + SQLAlchemy async + Alembic | ✅ | migrations reach `ec324056e4de` |
| PostgreSQL in production | ✅ verified | the full 231-test suite passes against PostgreSQL 16 (`IPTV_TEST_DATABASE_URL`), CI runs it too |
| Auth: Argon2id, TOTP, sessions, CSRF | ✅ | `test_auth.py` |
| Secrets encrypted at rest, masked logs | ✅ | `test_secrets.py`, diagnostics log test |
| SSRF guard | ✅ | 21-test suite |
| Public endpoint rate limiting | ✅ | `test_hardening.py` |
| Request ids + structured request log | ✅ | `test_diagnostics.py` |
| Backup + verified restore | ✅ SQLite and PostgreSQL | restore drill executed on both; `pg_dump`/`pg_restore` compared identical row counts |
| Preflight checks | ✅ | 17 tests, exit code 1 on failure; also available in the panel (`/system/check`, Overview card) |
| Dependency audit + static scan | ✅ Python only | `pip-audit`: no advisories; `bandit`: clean. Container OS packages are **not** scanned — see `SECURITY-AUDIT.md` |
| SBOM | ❌ not produced | — |
| Container image | ✅ | 3 container acceptance tests, UID 10001, 367 MB default |
| Container **with PostgreSQL** | ✅ | `scripts/verify_pg_container.py`: migrations at start, import, resolver, restart persistence, in-container backup (27 tables in the dump) |
| CI pipeline | ⚠️ written, **never executed** | repository has no remote |
| Deployment to Coolify | 🚫 blocked | needs owner GO |
| Real provider + TV acceptance | 🚫 blocked | needs provider credentials and a TV |

Legend: ✅ verified · ⚠️ implemented but not fully verified · ❌ absent · 🚫 blocked on the owner.

## What "done" would mean

The specification's own definition of done, restated honestly:

1. `https://iptv.joxit.ru` serves the login page over TLS — **blocked** (GO + credentials).
2. A device plays a real provider through OwnTV and VLC for 30 minutes without a stall — **blocked**
   (provider credentials).
3. A restore from backup has been performed on the deployed instance — **partially**: the drill
   exists and passes on SQLite, not on the server.
4. CI green on `main` — **blocked** (no remote).

Everything not on that list is either implemented and verified, or explicitly listed above as not
implemented. The gap between "works" and "done" is now entirely owner-side.
