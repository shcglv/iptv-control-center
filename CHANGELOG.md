# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.4] — 2026-11-26

### Fixed

- **A single malformed Telegram update could stall the bot.** `poll_once` advanced its offset
  *outside* the per-update `try`, so an update without a numeric `update_id` — or a non-dict element
  in the batch — escaped and the poller re-fetched the same batch forever: the bot would answer
  nothing and log the same error every five seconds. The offset now advances defensively and the
  handler ignores junk instead of raising.
- Non-numeric chat ids no longer produce an exception; such an update is simply skipped.

### Notes

- Robustness, not an exploitable hole: the real Bot API always sends a numeric `update_id`. The
  wedge needed a malformed payload, which is why this is a defence-in-depth fix rather than a
  vulnerability report. It is still the difference between a noisy log line and a silent outage.

## [1.6.3] — 2026-11-26

### Fixed

- **A single playlist line could block the server for minutes.** `parse_attributes` used a regular
  expression that retried the key pattern at every character, so an unterminated quote made one
  `#EXTINF` line quadratic: measured 27 ms at 1 000 characters, 1.25 s at 8 000, 5.2 s at 16 000 —
  and a 200 000-character line would have run for around thirteen minutes on the event loop,
  stalling every other request. Replaced with a single-pass scanner; the same 200 000-character line
  now parses in 0.08 s.
- A regression test feeds a 200 000-character unterminated attribute line followed by a valid entry
  and asserts parsing stays under five seconds and the valid entry survives.

### Notes

- Found by writing the test, not by reading the code: the audit question "does the parser survive a
  pathological line?" produced a hang on the first run.
- Real attribute payloads are under a kilobyte; the old code was fine in practice and dangerous in
  the worst case, which is exactly the case an attacker chooses.

## [1.6.2] — 2026-11-26

### Security

- **Request bodies have a size limit.** Imports arrive as JSON, and nothing stopped a client from
  streaming hundreds of megabytes into the process. `IPTV_MAX_REQUEST_BYTES` (64 MB default) is
  enforced by middleware from `Content-Length` before the body is buffered, and the two import
  handlers re-check the decoded string so a chunked upload without a length cannot bypass it.
- An oversized upload now returns `413` with the actual size, instead of consuming memory until the
  process is killed.

### Audited and found already sound

- Discovery fetches pages and feeds through the guarded client with an 8 MB cap.
- The RSS/Atom parser already used `resolve_entities=False` and `no_network=True`.
- Login is throttled per client key, and the session cookie is `HttpOnly`, `SameSite=Lax`, and
  `Secure` whenever the base URL is HTTPS.

## [1.6.1] — 2026-11-26

### Security

- **XML entity expansion is refused instead of trusted to a heuristic.** XMLTV parsing allowed a
  DOCTYPE (real exports carry one) while leaving libxml2's entity resolution on, so a
  "billion laughs" guide depended on libxml2's amplification limit rather than being impossible.
  Resolution is now off, and the amplification refusal is wrapped into the service's own `XmltvError`
  so a hostile upload answers `422` instead of a `500` with a traceback.
- Verified by a regression test with a six-level entity bomb: it is rejected in milliseconds, while
  the same document expanded to hundreds of thousands of characters with resolution enabled.

## [1.6.0] — 2026-11-26

### Security

- **The API schema is no longer public in production.** `/docs`, `/redoc` and `/openapi.json` were
  served to anyone who asked; they now exist only outside production, and `IPTV_ENABLE_DOCS=true`
  overrides that when a schema is genuinely needed. The SPA fallback returns `404` for those paths
  instead of answering with `index.html`, so a scanner sees "not served" rather than a misleading
  `200`.
- Verified in the production-shaped container: `/docs`, `/redoc` and `/openapi.json` all return 404
  while development keeps them, via `scripts/verify_panel.py`.

## [1.5.1] — 2026-11-26

### Security

- **Browser-hardening headers on every response.** A new `scripts/smoke_public.py` checked a running
  instance and found only three handlers set `X-Content-Type-Options`; the HTML shell and the health
  endpoint set nothing. A middleware now applies `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` and a restrictive `Permissions-Policy` to everything.
- `Strict-Transport-Security` is added only when `IPTV_PUBLIC_BASE_URL` is HTTPS, so development over
  plain HTTP is not pinned to a scheme it cannot serve.

### Added

- `scripts/smoke_public.py --base https://…` — a 5-check post-deployment smoke test: health, login
  page, a built asset, security headers, and TLS certificate lifetime. No credentials, no shell on
  the server. Against a non-IPTV site it fails correctly (negative control).

## [1.5.0] — 2026-11-26

### Added

- **System panel.** `GET /api/v1/system/info` reports version, environment, database engine,
  applied migration and entity counts; `GET /api/v1/system/check` runs the same eight checks as
  `scripts/preflight.py`, but from inside the running process. The Overview screen shows both, with
  a `Проверить` button.
- Useful exactly at deployment time: an operator can confirm the container is wired correctly
  (key, base URL, timezone, artifacts directory, database, migrations) without shell access.

### Notes

- The panel checks reuse the request's own session for database and migration verdicts, so they test
  the connection the app actually uses rather than a second one opened from configuration.
- An unmigrated database is reported as a failed check, not an error page: `/system/info` reads
  `alembic_version` inside a savepoint and returns `null` when the table does not exist yet.

## [1.4.1] — 2026-11-26

### Fixed

- **Duplicate merge failed on PostgreSQL.** Deleting the duplicate channel cascaded to the
  `match_suggestion` row that referenced it, so the following `UPDATE … status='accepted'` matched
  zero rows and raised `StaleDataError`. SQLite does not enforce foreign keys by default, which is
  why 251 SQLite tests passed while three failed on PostgreSQL. The merge now deletes the resolved
  suggestion explicitly before the channel and reads the decision from the database rather than a
  possibly-stale object.
- Consequence, now intended and documented: a resolved suggestion is removed with the duplicate, so
  accepting it twice returns `404`, not `409`. The queue holds open decisions only.

### Verified

- Full suite on **both** databases at 1.4.0/1.4.1: 251 passed on SQLite and PostgreSQL 16.
- PostgreSQL container drill at 1.4.0: migrations `ec324056e4de`, import, 302, restart persistence,
  78 KB dump with 28 table entries.
- Container acceptance on an image rebuilt from HEAD: 3 passed.

## [1.4.0] — 2026-11-25

### Added

- **Duplicate review queue.** A scan compares channels that share a first word, scores their
  normalised names, and records pairs in the 78–92 % band as suggestions — the range where a human
  should decide. Identical names are skipped (import already merges them) and anything above 92 % is
  treated as too confident to queue.
- Accepting a suggestion **merges** the two channels: stream links, profile overrides and the EPG
  mapping move to the kept channel, duplicate links are dropped rather than doubled, related
  suggestions are discarded, and the duplicate row is deleted. Rejecting keeps both.
- `POST /api/v1/matches/scan`, `GET /api/v1/matches`, accept/reject endpoints, and a "Дубликаты
  каналов" card on the Catalog screen with similarity, `Объединить` and `Это разные`.

### Notes

- Nothing merges automatically. A wrong automatic merge destroys a channel and its EPG mapping;
  leaving a duplicate is a cosmetic problem. The scan is therefore advisory by design.
- The service refuses to merge an already-resolved suggestion, not just the API: defence in depth
  after a test showed the service alone would happily re-run a merge.

## [1.3.0] — 2026-11-25

### Added

- **Category (group) visibility editor.** The profile screen now lists every category with its
  channel count and a checkbox; unchecking a group hides its channels for that profile's devices,
  while an individual channel can still be re-enabled below it.
- `GET /api/v1/profiles/{id}/categories` returns the effective visibility per category — the missing
  read side of an endpoint that could previously only be written.

### Notes

- The editor intentionally keeps category-hidden channels in the list and shows them as unchecked,
  instead of removing rows: an operator needs to see what a group is hiding in order to re-enable one
  channel from it. A browser test asserts exactly that distinction.

## [1.2.0] — 2026-11-25

### Added

- **Client Compatibility Engine.** Each device family declares which containers it can play, and the
  playlist only advertises those: `android_tv` gets ts + m3u8, `apple_tv` and `mobile` get HLS only,
  `desktop` gets everything, unknown kinds fall back to the conservative pair. A TS-only channel no
  longer appears in an Apple TV playlist as a channel that cannot play.
- The resolver refuses a container the client cannot handle instead of guessing, and the guide cap
  is now the smaller of `IPTV_EPG_MAX_BYTES` and the family's tolerance (5 MB Android TV, 8 MB Apple
  TV, 4 MB mobile).
- `tests/test_capabilities.py`: registry, fallback, cap arithmetic, playlist filtering per kind and
  resolver refusal.

## [1.1.0] — 2026-11-25

### Added

- **Smart-TV guide cap** (`IPTV_EPG_MAX_BYTES`, 5 MB default): when a device guide would exceed the
  cap, it is re-rendered with a narrower window — 7 days → 3 → 1 — instead of being truncated, so
  the document is always well-formed XML. Many TVs refuse or freeze on guides of tens of megabytes,
  and at 10 000 channels the full window reached 39 MB.
- If even a one-day window exceeds the cap, the size is logged and served rather than silently
  dropped: an oversized guide is better than no guide.

### Changed

- Guide rendering is split into `_render_guide` (one window) and `build_guide` (window selection),
  which is what makes the retry possible without buffering the document in memory.

## [1.0.2] — 2026-11-25

### Fixed

- **Active probing treated an undecodable stream as healthy.** Found against a real public HLS
  entry whose variant playlists return 404: ffprobe exits 0 with an empty `streams` array, and the
  probe only checked the exit code. `MediaInfo` now carries `stream_count`, and a zero-stream
  inspection fails with `ffprobe_no_streams`.
- Syntax damage from an earlier automated edit in `health.py` was caught by the test run and fixed
  before commit.

### Verified on real data

- A public 20.9 MiB XMLTV guide ingested as 287 channels and 48 814 programmes with zero skipped
  records.
- ffprobe read a real public HLS stream (1280×720, h264, 10 streams) inside the image, and correctly
  rejected the streamless manifest above. Details: [`docs/REAL-WORLD.md`](docs/REAL-WORLD.md).

## [1.0.1] — 2026-11-25

### Added

- Dependency audit (`pip-audit`) and static analysis (`bandit`) as a CI job, plus a weekly schedule
  so a new advisory in a pinned dependency surfaces without a push. First run: **no known
  vulnerabilities**, no bandit findings. Report: [`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md).

### Fixed

- `backup.py` built SQL from table names read out of the SQLite file without validating them. A
  crafted database file could not exploit it (identifiers were quoted), but the name is now checked
  against `[A-Za-z_][A-Za-z0-9_]{0,62}` before use.

## [1.0.0] — 2026-11-25

First release verified as an artifact. Not deployed; see [`RELEASE.md`](RELEASE.md).

### Verified for this release

| Suite | Result |
|---|---|
| Backend on SQLite | 232 passed, 9 skipped |
| Backend on PostgreSQL 16 | 232 passed, 9 skipped |
| Browser (Chromium, real server) | 6 passed |
| Container acceptance (image rebuilt from HEAD) | 3 passed |
| PostgreSQL container drill | passed — migrations at start, import, 302, restart persistence, 73 KB dump with 27 tables |
| SQLite restore drill | passed |
| ruff / mypy strict | clean |

### Changed

- Browser tests use a 30 s default timeout: one catalog test failed once while a Docker build starved
  the machine, and a loaded CI runner is not a product failure.
- Version 1.0.0 across the package and the image.

## [0.5.2] — 2026-11-25

### Added

- `scripts/verify_pg_container.py`: the shape Coolify will deploy, end to end — PostgreSQL by
  service name on a user network, migrations at container start, admin via the container CLI,
  import, playlist, 302 resolver, container restart with data intact, and a backup executed inside
  the application container. Executed: `alembic_version 198a8944b683`, 2 channels before and after
  the restart, 73 KB dump with 27 table entries.
- `--build-arg INSTALL_PG_CLIENT=true` puts `pg_dump`/`pg_restore` in the image.

### Fixed

- `scripts/backup.py` passed the SQLAlchemy URL (`postgresql+psycopg://…`) to `pg_dump`, which
  speaks libpq and silently fell back to a local socket. The driver suffix is now stripped.
- The image size table is now measured rather than estimated: default **367 MB**, +81 MB with the
  pg client, +610 MB with ffmpeg.

## [0.5.1] — 2026-11-25

PostgreSQL is now a supported, verified target rather than a documented intention.

### Added

- **`psycopg[binary]` as a core dependency.** The documented production URL
  `postgresql+psycopg://…` had no driver installed at all — the container would have failed at
  startup. This was found by actually pointing the app at PostgreSQL.
- The whole 231-test suite runs against PostgreSQL 16 via `IPTV_TEST_DATABASE_URL`; CI gained a
  `backend-postgres` job with a PostgreSQL service.
- `data/live_pg_backup_drill.py`: seed → `pg_dump` → `pg_restore --list` → restore into a fresh
  database → compare row counts.

### Fixed

- Windows + PostgreSQL: psycopg's async mode cannot use the default Proactor loop. The test harness,
  `python -m iptvcc`, and `migrations/env.py` now select the selector loop on Windows
  (a no-op on the Linux target).
- `probe_media` no longer needs `asyncio.create_subprocess_exec`, which the selector loop does not
  implement; ffprobe runs in a worker thread instead, so active probing works on every loop.
- `backup_postgres` streams `pg_dump` to stdout and accepts a command prefix
  (`IPTV_PG_DUMP="docker exec -i pg pg_dump"`), so the path is executable without host client tools.

## [0.5.0] — 2026-11-25

### Added

- **Active probe mode** (`IPTV_PROBE_MODE=active`): after a successful HTTP probe, ffprobe inspects
  the stream with `-show_streams -show_format` under a wall-clock and `-rw_timeout` bound, filling
  real codecs, resolution and bitrate. A failed inspection counts as a failed check — that is the
  point of the mode. Concurrency is capped by `IPTV_PROBE_CONCURRENCY` (default 2).
- The binary is **not** in the default image: build with `--build-arg INSTALL_FFMPEG=true`. Measured
  cost is **+628 MB** (349 → 977 MB), documented in `PERFORMANCE.md`; passive remains the default.

### Verified

- ffprobe 7.1.5 runs in the built image and the process still runs as UID 10001.
- Nine tests cover the command shape, JSON parsing, a missing binary, a non-zero exit, and that
  passive mode never invokes ffprobe.

## [0.4.1] — 2026-11-25

### Added

- **Request correlation**: every response carries `X-Request-ID` (an incoming one is reused, a
  generated one otherwise) and every log line includes it.
- **Structured request log**: one line per request with method, route **template**, status and
  duration. The template is used deliberately — a raw path would put device tokens in the log.
- **Channel diagnosis**: `GET /api/v1/channels/{key}/diagnose` returns every source with its score,
  uptime, probe count, per-container eligibility and the reason it is not used, the resolution for
  `ts`/`m3u8`/`mpd`, whether a take-down block hides the channel, and per-profile visibility. The
  panel exposes it through a "Почему?" button on the Channels screen.

### Fixed

- The diagnosis first reported no resolution for unprobed streams: a score of 0 tied with the
  "nothing selected yet" sentinel. Selection now treats "no candidate" as distinct from "score 0".

## [0.4.0] — 2026-11-25

### Added

- **Drag-and-drop channel ordering** in the profile editor: press the handle, move the row, release.
  The ↑/↓ buttons remain as the keyboard-accessible path and share the same logic.
- `POST /api/v1/profiles/{id}/order` — renumbers a lineup in one transaction, used when two
  neighbours already sit on consecutive integers so no midpoint exists.

### Fixed

- **The profile editor was sorted by channel number, not by the effective order.** Any reorder —
  drag or arrows — was persisted and then visually ignored, because the list re-rendered in catalog
  order. It now sorts by the same key the playlist uses.
- The ↑ arrow could move a row two positions when the neighbour's order value collided with the row
  above it; both arrows now insert at the neighbour's position instead of doing arithmetic on
  `sort_index`.

## [0.3.2] — 2026-11-25

XMLTV corpus hardening: 19 tests built from what providers actually emit.

### Added

- `tests/test_epg_corpus.py` — offset variants, truncated timestamps, DOCTYPE, a windows-1251
  declaration, BOM and CRLF, CDATA, missing `stop`, broken timestamps, duplicate channel ids,
  programmes for unknown channels, and re-import replacement.

### Fixed

- Timestamps written as `20261125180000+0300` (no space), `+03:00` (colon) or `Z` were rejected;
  date-only and seconds-less stamps (`20261125`, `202611251800`) are now padded instead of dropped.
- **DOCTYPE declarations were rejected.** Real XMLTV exports carry `<!DOCTYPE tv SYSTEM "xmltv.dtd">`,
  so a plain guide could fail to import. They are now accepted: `no_network=True` means the DTD is
  never fetched, and libxml2's amplification limits apply (`huge_tree=False`). The 50 MB document cap
  remains, and its test now pins the guard instead of relying on libxml's text-node limit.
- A document decoded to `str` but still declaring `encoding="windows-1251"` was re-encoded as UTF-8,
  turning every non-ASCII name into mojibake. The declaration is now dropped before encoding.

## [0.3.1] — 2026-11-25

Provider-corpus hardening: 17 tests built from realistic and hostile playlists.

### Added

- `tests/test_playlists.py` — single-quoted and unquoted attributes, commas inside quoted
  attributes, `#EXTGRP` before and after `#EXTINF`, `#EXTVLCOPT`, CRLF/BOM, URLs without `#EXTINF`,
  junk lines, Cyrillic names, duplicate `tvg-id`, one URL under two ids, windows-1251 without a
  charset header.
- Non-http URLs (`udp://`, `rtmp://`) are skipped and counted as `skipped` instead of being stored
  as unplayable streams. The import result in the UI shows the counter.

### Fixed

- Single-quoted `#EXTINF` attributes were parsed with the quotes included, so `tvg-id='x'` produced
  an id of `'x'`.
- The same URL appearing twice under two channel ids silently dropped the second channel; the stream
  stays one row and both channels now link to it.
- A pasted HLS **media** playlist (segments) imported every segment as a channel. It is now rejected
  with "this is an HLS media playlist … not a channel list".
- A playlist containing no URLs is rejected instead of reporting a successful empty import.
- Playlists in windows-1251 without a charset header were decoded as UTF-8 with replacement
  characters; decoding now falls back through cp1251/cp1252.

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
