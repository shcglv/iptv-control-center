# Compliance with the agreed design

Each row is a requirement from the approved specification, where it lives in the code, and where its
behaviour is proven. The numbers in the middle column are also asserted by `scripts/check_spec.py`, so
a refactor that changes them fails rather than drifts.

Run the mechanical check:

```powershell
.\.venv\Scripts\python.exe scripts\check_spec.py     # RESULT: SPECIFICATION INVARIANTS HOLD
```

## Streaming and delivery

| Requirement | Implementation | Evidence |
|---|---|---|
| The server never carries video; playback is a `302` to the provider | `api.resolve` returns `RedirectResponse`; no `StreamingResponse` anywhere | `check_spec.py` refuses `StreamingResponse`; `test_api`; container acceptance asserts the redirect target |
| A `.ts` URL must never receive a manifest | `delivery.resolve_stream` picks only same-container links | `test_delivery` / `test_m3u` container guard cases |
| The resolver refuses hidden channels | `resolve_stream` checks `Channel.is_hidden` | `test_api` hidden-channel case |
| Deterministic M3U: same input, same bytes | `m3u.render_m3u` sorts by profile order then number | `test_m3u` determinism case; profile render benchmark is repeatable |
| The playlist advertises its guide under every historical name | `url-tvg`, `x-tvg-url`, `tvg-url` | `check_spec.py`; `test_m3u` |
| `tvg-id` equals the XMLTV `<channel id>` | `delivery` maps through `ChannelEpgMap` | `test_epg` mapping and guide cases |
| An Apple TV is not offered MPEG-TS | per-family container sets | `test_capabilities` |

## Selection and quality

| Requirement | Implementation | Evidence |
|---|---|---|
| Score = 0.40 reliability + 0.15 startup + 0.25 technical + 0.10 compatibility + 0.10 errors | `quality.W_*` | `check_spec.py` asserts each weight and their sum; `test_quality` |
| Switch only on a +10 improvement | `quality.SWITCH_MARGIN = 10.0` | `check_spec.py`; `test_quality` hysteresis |
| Never switch within a 15-minute dwell | `quality.DWELL_SECONDS = 900` | `check_spec.py`; `test_quality` |
| Probe modes: passive by default, active opt-in | `settings.probe_mode`; ffprobe in a separate image | `test_health`, `test_active_probe`; real-stream drill |
| One provider is not hit by the whole batch | per-host probe semaphore | `test_health::test_probe_round_caps_concurrency_per_host` |

## Identity and catalog

| Requirement | Implementation | Evidence |
|---|---|---|
| Exact `tvg-id`, then normalised name, then a new channel | `services/importing` | provider corpus, `test_importing` |
| Ambiguous names are reviewed, never merged silently | 78–92 band → `MatchSuggestion`; ≥92 skipped | `check_spec.py` bands; `test_suggestions` |
| Take-down blocks remove a channel everywhere | `services/catalog.apply_blocklist` | `test_catalog` |

## Security

| Requirement | Implementation | Evidence |
|---|---|---|
| Argon2id passwords | `passwords.py` | `test_auth` |
| TOTP second factor | `totp.py` | `test_auth`, browser login |
| Server-side sessions, 12 h, hashed at rest | `auth.SESSION_TTL`, SHA-256 | `check_spec.py`; `test_auth` |
| CSRF header on state-changing requests | `auth.py` | `test_auth`, `test_hardening` |
| AES-256-GCM for stream URLs at rest | `secrets.py` | `test_api` round-trip |
| SSRF guard: scheme allowlist, DNS check, redirect re-check, metadata blocked | `netguard.py` | `test_netguard` |
| Public endpoints limited to 120 requests/min per token and address | `ratelimit.py` | `check_spec.py`; `test_hardening` |
| Request ids in logs, route templates rather than raw tokens | `logging_config.py` | `test_diagnostics` |
| No API schema in production | `docs_url` unset unless enabled | `verify_panel.py` asserts three 404s |
| Browser-hardening headers on every response | middleware | `test_hardening` |

## Operations

| Requirement | Implementation | Evidence |
|---|---|---|
| SQLite for development, PostgreSQL for production | SQLAlchemy async, psycopg | full suite on both |
| Migrations reach the documented head | Alembic | `check_docs.py` compares the quoted head with the real one |
| Backups can be restored, not merely written | `backup.py` | `verify_pg_container.py`, `verify_restore.py` |
| An upgrade preserves data | schema-only migrations | `verify_upgrade.py` |
| Smart TVs get a guide they can open | 7→3→1-day windows | `test_epg_cap` |
| One command reproduces the verification | `verify_all.py` | documented run at `72f980c` |

### Write failures

`SessionDep` uses FastAPI's `scope="function"`, so the dependency's commit runs **before** the
response is sent: a failed write aborts the request instead of returning success. That ordering is
FastAPI's documented behaviour, and the login route is the one path proven end to end — it commits
explicitly and answers `503 could not persist the session`.

The in-process transport used by the tests raises the exception either way, so it cannot distinguish
an abort before the response from an exception after it. This document does not claim more than that.

## Not covered by this table

Anything that needs a server, a provider or a television: the deployment itself, real provider
streams, a real Telegram delivery, and a real player. `docs/ACCEPTANCE.md` is the checklist for those,
and `docs/STATUS.md` lists what is deliberately absent.
