# Roadmap

Phase plan from the approved Architecture Plan v3.3. A phase is done only when its **Done when**
line is true and the tests exist.

Legend: `[x]` shipped and verified · `[~]` partially shipped · `[ ]` not started.

## P0 — Decisions and access — `[~]`

- [x] License decided: **AGPL-3.0-only**
- [x] MVP clients: **OwnTV 5.2.2 + VLC 3.x/4.x**
- [x] Forum adapter deferred to P7
- [x] Scale: 5–15 devices, 2 000–10 000 channels, 5–10 sources
- [x] Domain `iptv.joxit.ru` verified as free (no DNS record, not used by any Coolify app)
- [ ] GitHub repository created + GitHub App installed (blocked: no credentials on this workstation)
- [ ] Remaining owner decisions: TZ wording (C-02), single container vs api+worker (C-04)

## P1 — Foundation — `[~]`

- [x] Package skeleton, settings, async DB, models
- [x] Structured modules + unit/integration tests (36 passing)
- [x] `pyproject.toml`, `Dockerfile` (digest-pinned), `.dockerignore`, `.gitattributes`
- [x] AES-256-GCM secret storage
- [x] SSRF guard
- [x] Alembic migrations (initial schema, applied by `scripts/start.sh`)
- [x] Admin password + TOTP login, sessions, CSRF
- [x] Web UI shell: login, Overview, Channels, Quality, Sources, Devices (Prism tokens)
- [ ] Coolify resources: Database + Application + domain + backup + Kuma
- [x] CI: ruff, mypy, pytest, web typecheck/build, image build and container smoke test
- [x] Dependabot for pip, npm, actions and the base image
- [ ] SBOM and vulnerability scanning in CI

**Done when:** `https://iptv.joxit.ru` serves a login page over TLS, `/healthz` is green in Uptime
Kuma, the database backup has been restored once, and CI is green on `main`.

## P2 — Sources and catalog — `[~]`

- [x] M3U import (streamed parse, attribute handling, idempotent upsert)
- [x] Stale marking instead of deletion
- [x] Channel auto-creation and linking
- [x] Admin API for sources/channels
- [ ] Xtream provider adapter
- [ ] REST connector, manual single-URL source
- [ ] Durable job queue (Postgres advisory locks) and scheduled refresh
- [ ] Source history, availability stats, subscription dates
- [ ] Channels UI (list/grid, filters, drawer)

## P3 — Smart Matching — `[~]`

- [x] Normalisation, region extraction, fuzzy scoring, thresholds
- [x] Exact `tvg-id` → exact normalized name → new channel
- [ ] Review queue for 78–92 % matches
- [ ] Manual merge / split / reassign, per-field overrides
- [ ] iptv-org alias seeding (database CSV)

## P4 — Devices and delivery — `[~]`

- [x] Device + assignment + hashed token, instant revocation, rotation
- [x] Deterministic per-device M3U with all three EPG attrs
- [x] 302 resolver, container guard, HEAD
- [x] Playlist-profile visibility, ordering, category rules and source pins; playlist and guide share the lineup
- [ ] Client Compatibility Engine (capability registry, `RenderPlan`)
- [ ] Devices & Clients UI, QR, copy actions

## P4b — Playlist Studio — `[~]`

- [x] Profiles screen: create profile, per-channel visibility, up/down ordering, source pin with live scores
- [x] Profile changes reflect in the playlist and the guide on the next request
- [ ] Drag & drop ordering, category visibility editor, impact panel ("12 devices affected")
- [ ] M3U / EPG preview inside the editor

## P5 — EPG — `[~]`

- [x] XMLTV sources (`epg_source`), streaming ingest with `lxml.iterparse`, batched inserts
- [x] UTC epoch storage, offset parsing, per-source default timezone
- [x] Retention and idempotent full replace per source
- [x] Channel mapping: exact `tvg_id` → normalised name; manual maps preserved
- [x] Per-device `epg.xml` / `epg.xml.gz`, mapped ids emitted in the playlist
- [ ] Smart-TV size cap (≤ 5 MB) and profile-aware window
- [ ] EPG management view + TV guide view in the UI
- [ ] `iptv-org/epg` sidecar (optional, licence caveat)

## P6 — Health and quality — `[~]`

- [x] Light probe: DNS, HTTP with a 2 KB range read, HLS master parsing (resolution, codecs, bandwidth)
- [x] `health_check` samples and `stream_quality` aggregates (uptime, p90 TTFB, failures, last error)
- [x] Composite score with reliability > resolution (asserted by a test)
- [x] Anti-flapping: +10 margin and 15-minute dwell, persisted per channel
- [x] Resolver and playlist renderer use the ranking
- [x] Admin API: run a round, list stream health, inspect channel candidates
- [x] Autonomous scheduling: lease in `scheduler_state`, hot/cold intervals, watchdog
- [ ] Profile-based T0–T4 tiers (needs per-profile channel visibility)
- [ ] Provider connection-slot budget per source
- [ ] Active mode (ffprobe) behind an explicit toggle
- [ ] Quality Monitor UI and notifications

## P7 — Discovery — `[~]`

- [x] Extractors: M3U/M3U8, Xtream get.php/player_api, XMLTV, `#EXTM3U` bodies, base64 playlists
- [x] RSS/Atom and HTML adapters with pagination and provenance
- [x] Candidate store with dedupe, encrypted URLs, masked display, credential flag
- [x] Review API: run, list, accept (creates a Source), reject, status
- [x] Discovery UI: run form, filter chips, masked candidate table, accept/reject
- [ ] iptv-org catalog seeding + DMCA blocklist
- [ ] Authenticated forum sessions (cookie import)
- [ ] Scheduled re-crawls and change tracking of already-seen posts

## P8 — Telegram — `[~]`

- [x] Bot API adapter (isolated, configurable base URL, explicit proxy only)
- [x] Long polling, allowlist with owner bootstrap, `/start /help /status /channels /sources /problems /devices /subscriptions /report /check`
- [x] Notification scan: source errors, subscription expiry, scheduler silent/failing, EPG missing
- [x] Dedupe window, recovery messages, per-chat mute
- [ ] Rich messages / widgets / topics (Bot API 10.x extras)
- [ ] Daily scheduled report at a fixed local time

## P9 — Scale — `[x]`

- [x] Benchmark harness (`scripts/bench_scale.py`) for import, render, EPG ingest, mapping and guide build
- [x] 10 000 channels / 30 000 streams / 240 000 programmes measured — see `docs/PERFORMANCE.md`
- [x] Import ≈5× faster, render ≈2.5×, EPG ingest ≈4×, guide ≈2×
- [ ] Re-measure on PostgreSQL and on the target server
- [ ] Profile-based playlist render cache if a cold 1.5 s playlist becomes a problem

## P10 — Hardening — `[ ]`

SSRF suite, rate limits, log audit, restore drill, accessibility and visual regression, full Coolify
checklist, runbook.

## P11 — Extensions — `[ ]`

Xtream server endpoint, catch-up, worker split, plugin host, relay mode (opt-in), multi-user.
