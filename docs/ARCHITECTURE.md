# Architecture

**Status:** `0.1.0` vertical slice. This document is the single source of truth for how the
system is put together; the phase plan lives in `docs/ROADMAP.md`.

## 1. What this is

A self-hosted **control plane** for IPTV. It owns sources, one channel catalog, EPG, quality
scores and per-device playlists. It is not a media server: **video never transits it**.

```
provider playlist ──► import ──► streams ──► matching ──► channels
                                                             │
device /p/<token>/live.m3u ◄──── deterministic renderer ◄────┘
device /p/<token>/s/<key>.ts ──► 302 Location: provider URL ──► player ──► video (direct)
```

## 2. Invariants

1. **No media bytes.** No transcoding, no relay, no proxying in the default architecture.
2. **302 is not a proxy.** The resolver returns a redirect (tens of bytes) and closes; the player
   talks to the provider directly. See `docs/decisions/0001-direct-streaming.md`.
3. **Determinism.** Same catalog + profile ⇒ byte-identical M3U/XMLTV. Players key favourites and
   channel identity on `name|group` and `tvg-id`; churn breaks them.
4. **Containers never change behind a URL.** A `.ts` endpoint answers only with a `.ts` stream.
   Redirecting `.ts` to an HLS manifest breaks ExoPlayer and loops mpv (documented by OwnTV).
5. **Channels outlive sources.** A provider outage marks streams stale; it never deletes a channel.
6. **Secrets never travel.** Provider URLs are encrypted at rest; playlists carry only device
   tokens; logs show host + masked path.

## 3. Runtime shape

| Component | Now (0.1.0) | Target |
|---|---|---|
| API | one uvicorn process, FastAPI | unchanged |
| Migrations | Alembic, applied by `scripts/start.sh` | unchanged |
| Scheduler | not yet | in-process (APScheduler + PG advisory locks) |
| Worker | not yet | split into its own container only if load requires it (ADR-0003) |
| Database | SQLite for dev/tests, PostgreSQL 16 in production | unchanged |
| Cache | none | in-process + on-disk artifact cache |
| Reverse proxy | none in-repo | Coolify/Traefik (managed externally) |

Resource envelope (target): **384 MB / 0.6 CPU** for the API, **256 MB** for PostgreSQL.
Measured baselines on the host: postgres 15.9 MB RSS, comparable Python APIs 11–14 MB RSS.

## 4. Modules

| Module | Responsibility |
|---|---|
| `config.py` | env settings (`IPTV_*`), environment flags |
| `db.py` | async engine, session factory, dev schema bootstrap |
| `models.py` | sources, streams, channels, categories, devices, profiles, assignments |
| `secrets.py` | AES-256-GCM envelopes for values at rest, `mask_url` for logs |
| `m3u.py` | tolerant parser + deterministic renderer |
| `matching.py` | normalisation, region extraction, fuzzy scoring, thresholds |
| `quality.py` | composite score (reliability, startup, technical, compatibility, errors) |
| `netguard.py` | SSRF guard for any user-supplied URL (scheme, resolve, redirects, size) |
| `security.py` | device tokens (hashed), public keys, constant-time compare |
| `services/importing.py` | parse → upsert streams → attach to channels (idempotent) |
| `services/health.py` | light probes, `health_check` samples, `stream_quality` aggregates |
| `services/scheduler.py` | scheduler lease, tier intervals, watchdog |
| `services/selector.py` | candidate ranking and the anti-flapping decision |
| `services/epg.py` | XMLTV ingest, channel mapping, per-device guide rendering |
| `services/profiles.py` | effective lineup per playlist profile (visibility, order, pins) |
| `services/delivery.py` | per-device playlist rendering, 302 resolver |
| `services/discovery/` | extractors, RSS/HTML adapters, candidate store |
| `services/catalog.py` | channel identity catalog, matching pass, take-down blocks |
| `ratelimit.py` | sliding-window limits for the public `/p/…` endpoints |
| `backup.py` | online SQLite backup, `pg_dump` for PostgreSQL, verification |
| `telegram/` | Bot API adapter, long-polling bot, notification scan |
| `api.py` | HTTP surface: admin API, `/p/...`, `/healthz`, SPA |
| `web/` | React + TypeScript SPA (Vite), built into `src/iptvcc/static` |

## 5. Data model (current)

```
source 1──n stream n──n channel        (channel_stream: priority, role)
channel n──1 category
device 1──n assignment n──1 playlist_profile
assignment: token_hash (sha256), token_prefix, revoked_at
```

Permanent identity: `channel.id` (UUID) and `channel.public_key` (12 chars, used in URLs and as
`channel-id` for Jellyfin). Stream identity is `sha256(source_id, url)`, so re-imports are
idempotent and a changed URL becomes a new stream.

The parser accepts what providers actually ship: quoted or bare attributes, single quotes, commas
inside quoted values, `#EXTGRP` before or after `#EXTINF`, `#EXTVLCOPT`, CRLF and a BOM. It refuses
two things with an actionable message — an HLS *media* playlist (segments are not channels) and a
document with no URLs — and skips non-http entries (`udp://`, `rtmp://`) with a counter, because the
resolver can only redirect to http(s). Documents without a charset header are decoded as UTF-8,
then cp1251/cp1252, so a windows-1251 provider does not turn into replacement characters.

## 6. Delivery semantics

### Playlist

```
#EXTM3U url-tvg="…/epg.xml" x-tvg-url="…" tvg-url="…"
#EXTINF:-1 tvg-id="…" tvg-name="…" tvg-logo="…" tvg-chno="…" channel-id="…" group-title="…",Name
https://host/p/<token>/s/<public_key>.<ext>
```

All three EPG attributes are emitted because clients disagree: OwnTV prefers `url-tvg`, Kodi and
SS IPTV document `x-tvg-url`, Smart IPTV scans all three.

### Resolver

`GET /p/<token>/s/<key>.<ext>` → `302` + `Cache-Control: no-store`, or `503` when no healthy
stream of that container exists. `HEAD` answers `200` locally without touching the provider.

**Known limits (by design):**
- source switching cannot affect an already-open stream — the player is connected to the provider;
- provider credentials are visible to the device in the `Location` header;
- a source that requires a specific `User-Agent`/`Referer` cannot be served through a 302.

### EPG

Ingestion streams the XMLTV file with `iterparse` and inserts in batches, so memory is flat.
Programmes are stored as UTC epoch seconds — unambiguous on SQLite and PostgreSQL.

- A source is **fully replaced** on every import (programmes are deleted, then re-inserted), which
  makes re-import idempotent and applies retention in the same pass.
- `DOCTYPE` is accepted, because real exports carry `<!DOCTYPE tv SYSTEM "xmltv.dtd">`. The DTD is
  never fetched (`no_network=True`), entity amplification is limited by libxml2 (`huge_tree=False`)
  and the document is capped at 50 MB, which covers the attack surface without rejecting guides.
- Timestamps accept `+HHMM` or `+HH:MM`, with or without a space, `Z`, and truncated forms
  (`YYYYMMDD`, `YYYYMMDDhhmm`) which are padded; an unparseable timestamp skips that programme only.
- A document decoded to `str` has its `encoding=…` declaration dropped before UTF-8 encoding, so a
  windows-1251 guide does not become mojibake.
- Channel mapping: exact `tvg_id` first, then normalised display name. Manual mappings
  (`channel_epg_map.is_manual`) are never overwritten by an import.
- The playlist emits the **mapped** guide id, so `tvg-id` in the M3U and `<channel id>` in the
  XMLTV always agree — the join every player depends on.
- `GET /p/<token>/epg.xml` and `.xml.gz` render the device's own channel subset, cached for 5
  minutes on disk keyed by a hash of the token (the token itself never appears in a filename).

## 6.3 Health, quality and selection

**Probe (light mode).** DNS lookup, then one HTTP request with `Range: bytes=0-2047` that is read,
measured to first byte, and closed. It never opens a sustained media connection. If the body starts
with `#EXTM3U`, the master playlist yields `RESOLUTION`, `CODECS` and `BANDWIDTH` — so format is
known without ffprobe. Connect time is not measured separately (that would cost a second
connection); `ttfb_ms` covers connect + request + first byte.

**Aggregates.** Every probe writes a `health_check` row and refreshes `stream_quality`:
`uptime_ratio`, `avg_ttfb_ms`, `p90_ttfb_ms` over the last 50 successful samples,
`consecutive_failures`, last error class, and the last observed format. Samples are pruned after
seven days.

**Score.**

```
score = 100 * (0.40*reliability + 0.15*startup + 0.25*technical + 0.10*compatibility + 0.10*errors)
technical = 0.55*resolution + 0.30*codec + 0.15*bitrate_adequacy
```

A stable 720p stream beats a flapping 4K one — that is asserted by a test, not just documented.
FPS is absent because it needs ffprobe (optional active mode).

**Anti-flapping.** The selector keeps the channel's current stream unless a candidate is better by
at least 10 points **and** the current choice is older than 15 minutes (or is dead). The current
choice is remembered on `channel.active_stream_id` / `active_since_epoch`.

**Where it is used.** The resolver picks the best stream of the requested container. The playlist
renderer uses the ranking across all containers to decide which extension to advertise, so the
advertised suffix always matches the stream a player will receive.

**Scheduling.** An in-process task starts with the API and claims a lease before each round. Thelease is a conditional `UPDATE` on a single `scheduler_state` row (`locked_until < now OR owner =
me`), which is atomic on SQLite and PostgreSQL without database-specific advisory-lock functions, so
a second replica cannot double-probe. Due-ness is age based: streams of channels served within 24 h
are *hot* (every 5 min by default), everything else *cold* (hourly).

The watchdog reads the same row: if no tick arrived within `max(3 × tick, 300)` seconds, the status
reports `healthy: false`. A manual `POST /api/v1/scheduler/tick` is refused while the loop owns the
lease — which is also the live proof that the lease works.

## 6.4 Playlist profiles

A profile stores **overrides only**. A channel without a `profile_channel` row is visible with its
catalog number, so a new profile starts with the full lineup and no seeding of thousands of rows.

| Rule | Behaviour |
|---|---|
| Channel visibility | `profile_channel.visible = false` hides it; deleting the row restores the catalog default |
| Category visibility | `profile_category.visible = false` hides the category's channels, **unless** a channel is explicitly re-enabled |
| Ordering | `sort_index` when set, otherwise the channel number, then id — deterministic |
| Reordering | pointer drag on the handle or the ↑/↓ buttons; a single midpoint write when an integer slot exists, otherwise one `POST /profiles/{id}/order` renumbers the visible lineup |
| Group name | `profile_category.display_name` renames the group in the M3U |
| Source pin | `profile_channel.pinned_stream_id` wins when that stream is playable in the requested container; otherwise ranking takes over |

The same resolution feeds both the playlist and the per-device XMLTV, so a hidden channel disappears
from the playlist and from the guide together.

## 6.5 Web UI

React 19 + TypeScript built by Vite. The bundle is written into the Python package
(`src/iptvcc/static`), so one container serves both the API and the UI — no second service, no CDN,
no node runtime in production.

| Concern | Decision |
|---|---|
| Build | `cd web && pnpm install && pnpm build` (Node stage in the Dockerfile) |
| Serving | `/assets/*` is a static mount; every other non-API path returns `index.html` |
| Route order | the SPA catch-all is registered **last**; declared earlier it shadows every API route |
| Auth | cookie session; the CSRF token from login is kept in `sessionStorage` and sent as `X-CSRF-Token` |
| API paths | `/api/...` and `/p/...` are explicitly excluded from the SPA fallback and stay JSON 404s |
| Bundle size | 244 KB JS (75 KB gzip) + 6.9 KB CSS (2.1 KB gzip) |

Screens: Overview (stats, attention, worst streams), Channels (search + pagination), Quality
(probe scores and a manual check), Discovery (run an adapter, review candidates, accept or reject),
Playlists (profiles, per-channel visibility, ordering, source pins), Catalog (import identities,
match channels, manage take-down blocks), Sources (create + import playlist), Devices (create, reveal
token once, rotate). Login is password + TOTP.

The UI is verified in a real browser: `tests/test_browser.py` starts the server as a subprocess,
creates an admin with a known TOTP secret, logs in through Chromium, walks every section, edits a
profile and runs a discovery review against a local feed. It is opt-in because it needs a Chromium
download:

```powershell
$env:IPTV_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_browser.py -q
```

## 6.6 Telegram

Long polling, so no inbound port, no webhook and no public route are needed. The bot starts with the
API when `IPTV_TELEGRAM_BOT_TOKEN` is set and stops with it.

| Concern | Decision |
|---|---|
| API isolation | only `telegram/client.py` knows HTTP, method names and the token; `base_url` is a setting so tests and a self-hosted Bot API server work unchanged |
| Proxy | explicit `IPTV_TELEGRAM_PROXY` only; `trust_env=False`, because ambient `HTTP_PROXY` broke localhost in this environment |
| Access | allowlist in `telegram_user`; the first message from `IPTV_TELEGRAM_OWNER_ID` bootstraps it |
| Commands | `/start /help /status /channels /sources /problems /devices /subscriptions /report /check` — all read-only except `/check`, which runs one probe round |
| Notifications | scan every 5 min: source errors, subscription expiry ≤ 7 days, scheduler silent or failing, channels without EPG |
| Dedupe | stable key per problem, at most one alert per 6 h, one recovery message when it clears, per-chat mute honoured |

Verified against a fake Bot API: 19 unit/integration tests plus a live run that registers the
commands, polls, and delivers `🟡 Планировщик проверок ещё не запускался` to the allowlisted chat.

## 6.7 Source discovery

Discovery finds candidate links and keeps them out of the catalog until reviewed.

```
feed / forum page ──► adapter ──► extractors ──► candidate store ──► review ──► Source
```

| Piece | Behaviour |
|---|---|
| Extractors | `#EXTM3U` bodies, `.m3u/.m3u8`, Xtream `get.php`/`player_api.php`, XMLTV links, base64-embedded playlists (decoded, then accepted only if it really is a playlist) |
| Credential handling | a URL carrying `username`/`password` is flagged `has_credentials`; only a **masked** form is stored for display and returned by the API. The raw URL is encrypted at rest with the same envelope as stream URLs |
| RSS/Atom adapter | parses `item`/`entry`, keeps feed URL, item title, author and publication date as provenance |
| HTML adapter | anchors, `<pre>/<code>/<textarea>` blocks and plain page text; follows `rel=next` for at most three pages |
| Dedupe | `sha256(url)` unique; re-running a feed updates `last_seen_at` instead of duplicating |
| Review | candidates are `new` until accepted (creates a `Source` with the inferred type) or rejected; accepting twice returns 409 |

Adapters fetch through `netguard`, so SSRF rules apply to discovery exactly as to imports.
`IPTV_ALLOW_PRIVATE_NETWORK_TARGETS` relaxes the address check for LAN sources and is off by default.

## 6.8 Channel identity and blocks

Playlists name channels inconsistently; the catalog supplies an identity that does not depend on a
provider's spelling.

| Piece | Behaviour |
|---|---|
| Catalog import | `id`, `name`, `alt_names`, `country`, `categories`, `logo`, `website` — the iptv-org channel shape, accepted as-is by `POST /api/v1/catalog/import` or `scripts/catalog_import.py` |
| Matching | exact normalised name or alias; **country tie-break** when several catalogs share a name (`uk` is treated as `gb`). Deliberately no fuzzy matching — silently merging two real channels is worse than leaving one unmatched |
| Effect | `channel.catalog_id` is set; a channel without `tvg_id` inherits the catalog id, which is what the guide needs |
| Blocks | `catalog_id`, `tvg_id`, `name` hide a channel; `domain` disables a source. Hiding removes the channel from playlists, guides **and** the resolver |
| Re-application | A block is applied when created and can be re-applied to the whole catalog; block rows are never deleted by the block itself |

Matching is idempotent: already matched channels are counted as `already` and never re-pointed.

## 6.9 Diagnostics

Two things make a failure explainable instead of mysterious.

**Request correlation.** Every response carries `X-Request-ID`; an incoming value is reused (bounded
to 64 characters), otherwise a 12-hex id is generated. Every log line is prefixed with it. The
request log records method, **route template**, status and duration — never the raw path, because
`/p/<token>/live.m3u` would put a device token in the log. A 404 has no route, so only its first
path segment is logged.

**Channel diagnosis.** `GET /api/v1/channels/{key}/diagnose` answers "why is this channel not
playing":

| Field | Meaning |
|---|---|
| `streams[]` | every source with score, uptime, probe count, per-container eligibility and a reason list ("помечен устаревшим", "отключён вручную", "ещё не проверялся") |
| `resolution` | which stream id would be chosen for `ts`, `m3u8` and `mpd` — a `.ts` channel shows `null` for the manifest extensions |
| `hidden` / `blocked_by` | whether a take-down block is in force, and which one |
| `profiles[]` | per-profile visibility, including category rules |

The panel exposes this through a "Почему?" button on the Channels screen.

## 7. Security

| Concern | Control |
|---|---|
| Admin surface | Argon2id password + TOTP, server-side sessions (opaque token, SHA-256 at rest, 12 h TTL), `HttpOnly`/`SameSite=Lax` cookie, CSRF header on every unsafe method, 5 attempts/min login guard |
| Device access | 32-byte opaque token, SHA-256 at rest, prefix for lookup, instant revocation |
| Credentials at rest | AES-256-GCM (`IPTV_ENCRYPTION_KEY`, 32 bytes base64) |
| SSRF | scheme allowlist, DNS resolution check, redirect re-check, 50 MB cap, metadata hosts denied; link-local and cloud-metadata addresses stay blocked even when LAN targets are enabled |
| Public endpoints | sliding-window limit of 120 requests/min per device token and client address; `429` with `Retry-After` |
| Logs | `mask_url()` keeps scheme+host only |
| Container | non-root UID 10001, no build tools in the final layer, healthcheck |
| Backups | online SQLite backup / `pg_dump` custom format, integrity verified before the file is accepted |

## 8. Deployment

Coolify resource shape (unchanged from the approved plan): **1 Database** (PostgreSQL 16) +
**1 Application** (Dockerfile from this repo, port 8000, domain `iptv.joxit.ru` DNS-only).
No new infrastructure components. Builds run in Coolify; releases also publish an immutable
`ghcr.io/...@sha256:...` image as a fallback.

## 10. Build, CI and release

| Step | Command | What it proves |
|---|---|---|
| Lint | `ruff check` + `ruff format --check` | style and import hygiene |
| Types | `mypy src` | strict typing of the whole package |
| Tests | `pytest -q` | 115 tests on SQLite, including local HTTP fixtures |
| Browser | `pytest tests/test_browser.py` (`IPTV_BROWSER_TESTS=1`) | the UI works in Chromium against a real server process |
| Web | `pnpm typecheck && pnpm build` | the SPA compiles and produces `src/iptvcc/static` |
| Image | `docker build` | the multi-stage build, including the Node UI stage |
| Container | `pytest tests/test_container.py` (`IPTV_CONTAINER_TESTS=1`) | the image itself: admin CLI, import, profile, device playlist, 302 resolver, guide, discovery, restart persistence, UID 10001 |

`.\scripts\check.ps1` runs the local steps; `.github/workflows/ci.yml` runs the same plus the image
build and the container acceptance test on every push and pull request. Versions follow SemVer and
are recorded in `CHANGELOG.md`; the package version lives in `pyproject.toml` and
`iptvcc.__version__`.

The image is verified locally with `.\scripts\smoke-container.ps1`: 347 MB, healthy in ~5 s,
`/` serves the SPA, `create-admin` writes to the migrated database, and `id -u` is 10001.

The build itself is where three packaging defects surfaced — a missing runtime dependency
(`httpx`), a host `node_modules` leaking into the context, and TypeScript 7's platform packages
breaking `--frozen-lockfile` in Linux. All three are fixed and covered by the smoke test.

## 11. What is not implemented yet

Active probe mode (ffprobe) · Playlist Studio drag-and-drop · discovery/forum adapters ·
Telegram rich messages/widgets · CI pipeline · Coolify resources.
Tracked in `docs/ROADMAP.md`.

## 10. Reproducibility

The Dockerfile pins the runtime image by digest (`python:3.12-slim@sha256:191083cd…`), installs
with `--no-cache-dir` and downloads nothing at container start. Dependencies are pinned by lower
bounds in `pyproject.toml` until a lock file lands in P1.
