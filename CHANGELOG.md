# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-11-25

### Added

- **Channel identity catalog** — import the public channel catalog (iptv-org shape: `id`, `name`,
  `alt_names`, `country`, `categories`, `logo`, `website`), then match channels by exact normalised
  name or alias with a country tie-break (`uk` is treated as `gb`). Unmatched channels keep their
  placeholder identity rather than being fuzzy-merged.
- **Take-down blocks** — `catalog_id`, `tvg_id` or `name` hide a channel (playlist, guide and
  resolver); `domain` disables a source. Blocks are idempotent and re-applicable.
- `POST /api/v1/catalog/import`, `POST /api/v1/catalog/match`, `GET /api/v1/catalog/status`,
  `GET/POST/DELETE /api/v1/blocklist`, and `scripts/catalog_import.py` for large files.
- **Catalog screen** — counters (catalog / channels / matched / unmatched / blocks), JSON or file
  import, one-click matching, and blocklist management, covered by a browser test.
- **Preflight** — `scripts/preflight.py` checks the encryption key (including the public development
  key), public URL, timezone, Telegram token shape, rate limit, artifacts directory, database
  connectivity and that migrations are at head. Exit code 1 on failure.
- **Owner documentation** — `docs/ACCEPTANCE.md` (manual pass with a real provider and TV) and
  `docs/DEPLOYMENT.md` (Coolify plan, rollback, risk register, GO checklist; not executed).

### Changed

- The resolver refuses hidden channels, so a block cannot be bypassed with a known public key.

## [0.2.1] — 2026-11-25

Performance work measured at the top of the target range (10 000 channels, 30 000 streams,
240 000 programmes). Details and methodology: [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md).

### Changed

- Import: one flush for all new channels, chunked link preloading, bulk inserts for streams and
  links, batched heartbeat updates — 43.4 s → 7.7 s cold, 6.9 s → 3.9 s on re-import.
- Playlist render: links are read as a flat projection instead of a full ORM graph — 3.7 s → 1.5 s
  for 10 000 channels.
- EPG ingest: bulk `INSERT` for programmes, one flush for all pending channels — ≈4× faster.
- Guide build: programmes fetched 200 channels at a time with column projections; timestamps are
  formatted from `time.gmtime` instead of `strftime` — 21 s for 39 MiB of XML at 10 000 channels.
- Guides are cached per **profile** rather than per device, so devices sharing a profile share one
  render.

### Fixed

- `ingest_xmltv` crashed with `TypeError` when handed a `str` instead of `bytes`; both are accepted.
- A repeated URL inside one playlist could create a duplicate stream row.

### Added

- `scripts/bench_scale.py` — reproducible benchmark for import, render, EPG ingest, mapping and
  guide build.
- Container acceptance test (`tests/test_container.py`): builds the image, creates the admin through
  the CLI, imports a playlist, hides a channel in a profile, fetches the device playlist and guide,
  checks the 302 resolver, runs discovery against a host feed, accepts a candidate, restarts the
  container and verifies the data survived. Wired into CI as the `docker` job.

## [0.2.0] — 2026-11-25

Second working slice: the control plane is operable from a browser and a Telegram chat.

### Added

- **Playlist profiles** — per-device visibility, order, category rules and source pins; overrides
  only, so a new profile starts from the full catalog. One lineup resolver feeds both the playlist
  and the per-device XMLTV.
- **Web UI** — React 19 + TypeScript served by the API: login (password + TOTP), Overview, Channels,
  Quality, Discovery, Playlists, Sources, Devices.
- **Discovery** — extractors for `#EXTM3U` bodies, `.m3u/.m3u8`, Xtream endpoints, XMLTV links and
  base64 playlists; RSS/Atom and HTML adapters; candidate store with encrypted URLs, masked display
  and a review queue that creates sources only on accept.
- **Telegram bot** — long polling, allowlist with owner bootstrap, ten commands, and deduplicated
  alerts (source errors, subscription expiry, scheduler silent/failing, EPG gaps) with recovery
  messages and per-chat mute.
- **Subscription dates** — `source.subscription_expires_at`, surfaced in the API and `/subscriptions`.
- **SSRF knob** — `IPTV_ALLOW_PRIVATE_NETWORK_TARGETS` for LAN sources, off by default.
- **CI** — GitHub Actions: ruff, mypy, pytest, web typecheck/build, image build and a container
  smoke test that checks the UI is served, the CLI works and the process runs as UID 10001.
- **Browser tests** — Playwright: login with TOTP, navigation, profile editing and a discovery
  review, all against a real server process.

### Changed

- Networking clients (`netguard`, Telegram) no longer follow ambient `HTTP_PROXY`; an explicit proxy
  must be configured. This removes 502s on localhost calls seen in this environment.
- Dates in the UI render in `ru-RU`.
- TypeScript pinned to `~5.9`: TypeScript 7 pulls `@typescript/typescript-*` platform packages that
  break `pnpm install --frozen-lockfile` in a Linux build container.
- Build context excludes `web/node_modules`, `web/dist` and `src/iptvcc/static`, so a host-built tree
  can never leak into the image.

### Fixed

- SPA catch-all shadowed every API route when registered early; it is now registered last and a test
  asserts unknown API paths stay JSON 404s.
- Discovery's `CandidateOut` shadowed the health schema, breaking candidate listing.
- Duplicate candidates could violate the unique index when an adapter revisited a page.
- Autogenerated migrations that emit `postgresql.JSONB(astext_type=Text())` missed the `Text`
  import; the Alembic template now includes it.
- `httpx` was imported at runtime (`netguard`, Telegram, discovery) but only declared as a dev
  dependency, so the built image could not start. It is now a core dependency.
- `pnpm build` prompted for a modules purge in the image because there was no TTY; the web stage now
  sets `CI=true`.

## [0.1.0] — 2026-11-24

First working slice.

### Added

- FastAPI + SQLAlchemy 2.0 async + Alembic; PostgreSQL in production, SQLite for tests.
- M3U import with tolerant parsing, stream hash upsert and stale marking.
- Channel catalog, sources, devices with hashed tokens and instant rotation.
- Deterministic per-device M3U with `url-tvg`, `x-tvg-url` and `tvg-url`.
- 302 resolver with a container guard (a `.ts` URL never receives a manifest).
- Quality pipeline: P90 TTFB, uptime, composite score, anti-flapping selector with dwell time.
- Autonomous probe scheduler with a DB lease and a watchdog.
- XMLTV ingest, channel mapping and per-device guide rendering.
- Admin login: Argon2id, TOTP, server-side sessions, CSRF header.
- Dockerfile with digest-pinned bases and a non-root runtime user.
