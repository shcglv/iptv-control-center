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
- [ ] Web UI shell (Prism tokens + navigation)
- [ ] Coolify resources: Database + Application + domain + backup + Kuma
- [ ] CI: ruff, mypy, pytest, docker build, Trivy, SBOM

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
- [ ] Client Compatibility Engine (capability registry, `RenderPlan`)
- [ ] Playlist-profile filtering and ordering
- [ ] XMLTV endpoint per device
- [ ] Devices & Clients UI, QR, copy actions

## P4b — Playlist Studio — `[ ]`

Drag & drop editor, impact panel, M3U/EPG preview.

## P5 — EPG — `[~]`

- [x] XMLTV sources (`epg_source`), streaming ingest with `lxml.iterparse`, batched inserts
- [x] UTC epoch storage, offset parsing, per-source default timezone
- [x] Retention and idempotent full replace per source
- [x] Channel mapping: exact `tvg_id` → normalised name; manual maps preserved
- [x] Per-device `epg.xml` / `epg.xml.gz`, mapped ids emitted in the playlist
- [ ] Smart-TV size cap (≤ 5 MB) and profile-aware window
- [ ] EPG management view + TV guide view in the UI
- [ ] `iptv-org/epg` sidecar (optional, licence caveat)

## P6 — Health and quality — `[ ]`

Probes (passive/light/active), T0–T4 scheduler with a provider-slot budget, scoring
(`0.40R + 0.15S + 0.25T + 0.10C + 0.10E`), strategies, hysteresis, watchdog, Quality UI.

## P7 — Discovery — `[ ]`

iptv-org seeding + blocklist, forum adapter framework, extractors, Candidate Store, review UI.

## P8 — Telegram — `[ ]`

Bot API 10.5 compatibility layer, 16 commands, 18 templates, lifecycle/idempotency, notifications,
daily report, optional widgets/topics.

## P9 — Hardening — `[ ]`

SSRF suite, rate limits, log audit, restore drill, load test, accessibility and visual regression,
full Coolify checklist, runbook.

## P10 — Extensions — `[ ]`

Xtream server endpoint, catch-up, worker split, plugin host, relay mode (opt-in), multi-user.
