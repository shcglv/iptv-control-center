# Status against the specification

**Version 0.5.0 · commit `d484a86` · 2026-11-25**

What exists, what does not, and how each claim is verified. "Verified" means a test or a recorded
run proves it; "implemented" means the code exists but no test or run has exercised it end to end.

## Core product

| Requirement | Status | Evidence |
|---|---|---|
| **Direct streaming** — the server never carries video | ✅ | 302 resolver returns `Location: <provider URL>`; container test asserts the redirect target; no media code in the image |
| Pure-Direct alternative (playlist emits provider URLs) | ❌ not implemented | Profile flag exists in the plan, not in code |
| M3U import, tolerant parser, idempotent re-import | ✅ | 17-test provider corpus + import tests |
| Stale marking instead of deletion | ✅ | `test_api.py` |
| Channel catalog, permanent identity, public keys | ✅ | model + tests |
| Matching: exact `tvg-id`, normalised name, fuzzy thresholds | ✅ | `test_matching.py` |
| Fuzzy **review queue** (78–92 %) | ❌ not implemented | — |
| Manual merge / split / field overrides | ❌ not implemented | — |
| Playlist profiles: visibility, order, category rules, source pins | ✅ | `test_profiles.py`, drag browser test |
| Drag-and-drop ordering with keyboard fallback | ✅ | browser test |
| Category visibility editor, impact panel, in-editor preview | ❌ not implemented | — |
| Devices: hashed token, instant rotation/revocation | ✅ | `test_api.py` |
| Deterministic per-device playlist, all three EPG attributes | ✅ | container test |
| **Client Compatibility Engine** (`RenderPlan`) | ❌ not implemented | — |
| XMLTV ingest: streaming, timezone offsets, retention, replace | ✅ | 19-test EPG corpus |
| Channel↔EPG mapping, mapped ids emitted in the playlist | ✅ | `test_epg.py`, container test |
| Per-device guide, cached per profile | ✅ | container test, `test_profiles.py` |
| Smart-TV guide size cap | ❌ not implemented | — |
| Health probes, aggregates, composite score | ✅ | `test_health.py` |
| Anti-flapping (+10 margin, 15-min dwell) | ✅ | `test_quality.py`, `test_scheduler.py` |
| Autonomous scheduler with DB lease and watchdog | ✅ | `test_scheduler.py` |
| Active probe mode (ffprobe) | ✅ code, ⚠️ unverified against a real provider | `test_active_probe.py` (fake binary); image built with ffprobe 7.1.5 |
| Provider connection-slot budget | ❌ not implemented | — |
| Source Discovery: extractors, RSS/HTML, candidate review | ✅ | `test_discovery.py`, browser test |
| Channel identity catalog + take-down blocks | ✅ | `test_catalog.py`, browser test |
| Authenticated forum sessions, scheduled re-crawls | ❌ not implemented | — |
| Telegram bot: commands, alerts, dedupe, recovery | ✅ | `test_telegram.py` + live run against a fake Bot API |
| Rich messages / widgets / topics, daily scheduled report | ❌ not implemented | — |
| Web UI | ✅ 9 screens | 6 browser tests |

## Platform

| Requirement | Status | Evidence |
|---|---|---|
| FastAPI + SQLAlchemy async + Alembic | ✅ | migrations reach `198a8944b683` |
| PostgreSQL in production | ⚠️ configured, never run | all tests use SQLite |
| Auth: Argon2id, TOTP, sessions, CSRF | ✅ | `test_auth.py` |
| Secrets encrypted at rest, masked logs | ✅ | `test_secrets.py`, diagnostics log test |
| SSRF guard | ✅ | 21-test suite |
| Public endpoint rate limiting | ✅ | `test_hardening.py` |
| Request ids + structured request log | ✅ | `test_diagnostics.py` |
| Backup + verified restore | ✅ SQLite, ⚠️ PostgreSQL path untested | restore drill executed |
| Preflight checks | ✅ | 17 tests, exit code 1 on failure |
| Container image | ✅ | 3 container acceptance tests, UID 10001, 349 MB |
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
