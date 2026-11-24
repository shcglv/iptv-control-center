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
| `netguard.py` | SSRF guard for any user-supplied URL (scheme, resolve, redirects, size) |
| `security.py` | device tokens (hashed), public keys, constant-time compare |
| `services/importing.py` | parse → upsert streams → attach to channels (idempotent) |
| `services/epg.py` | XMLTV ingest, channel mapping, per-device guide rendering |
| `services/delivery.py` | per-device playlist rendering, 302 resolver |
| `api.py` | HTTP surface: admin API, `/p/...`, `/healthz` |

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
- `DOCTYPE` declarations are refused: XMLTV never needs them, and they are the entity-expansion
  attack surface.
- Channel mapping: exact `tvg_id` first, then normalised display name. Manual mappings
  (`channel_epg_map.is_manual`) are never overwritten by an import.
- The playlist emits the **mapped** guide id, so `tvg-id` in the M3U and `<channel id>` in the
  XMLTV always agree — the join every player depends on.
- `GET /p/<token>/epg.xml` and `.xml.gz` render the device's own channel subset, cached for 5
  minutes on disk keyed by a hash of the token (the token itself never appears in a filename).

## 7. Security

| Concern | Control |
|---|---|
| Admin surface | Argon2id password + TOTP, server-side sessions (opaque token, SHA-256 at rest, 12 h TTL), `HttpOnly`/`SameSite=Lax` cookie, CSRF header on every unsafe method, 5 attempts/min login guard |
| Device access | 32-byte opaque token, SHA-256 at rest, prefix for lookup, instant revocation |
| Credentials at rest | AES-256-GCM (`IPTV_ENCRYPTION_KEY`, 32 bytes base64) |
| SSRF | scheme allowlist, DNS resolution check, redirect re-check, 50 MB cap, metadata hosts denied |
| Logs | `mask_url()` keeps scheme+host only |
| Container | non-root UID 10001, no build tools in the final layer, healthcheck |

## 8. Deployment

Coolify resource shape (unchanged from the approved plan): **1 Database** (PostgreSQL 16) +
**1 Application** (Dockerfile from this repo, port 8000, domain `iptv.joxit.ru` DNS-only).
No new infrastructure components. Builds run in Coolify; releases also publish an immutable
`ghcr.io/...@sha256:...` image as a fallback.

## 9. What is not implemented yet

Quality engine · playlist-profile channel filtering · Playlist Studio · web UI · discovery/forum
adapters · Telegram bot · notifications · CI pipeline · Coolify resources.
Tracked in `docs/ROADMAP.md`.

## 10. Reproducibility

The Dockerfile pins the runtime image by digest (`python:3.12-slim@sha256:191083cd…`), installs
with `--no-cache-dir` and downloads nothing at container start. Dependencies are pinned by lower
bounds in `pyproject.toml` until a lock file lands in P1.
