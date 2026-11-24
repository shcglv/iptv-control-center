# IPTV Control Center

Self-hosted control plane for IPTV: sources, a single channel catalog, EPG, quality scoring and
per-device playlists. **Video never transits the server** — playlists point at provider URLs, and the
server's only role in playback is an optional HTTP 302 that picks the best source at request time.

> **Status: `0.1.0` — vertical slice.** Working: M3U import → channel catalog → playlist profiles
> (visibility, order, source pins) → per-device playlist → quality-aware 302 source resolution →
> XMLTV ingest with channel mapping and per-device guide output, autonomous health probing, admin
> login with TOTP, and a React web UI. Not yet: discovery, Telegram, CI. See
> [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What it does today

```
provider M3U ──► import ──► streams ──► matching ──► channels
                                                       │
device /p/<token>/live.m3u ◄── render (deterministic) ─┘
device /p/<token>/s/<key>.ts ──► 302 Location: provider URL ──► player ◄── video (direct)
```

- **Import** — tolerant M3U/M3U8 parser (`tvg-*`, `group-title`, `#EXTGRP`, `#EXTVLCOPT`), stable
  stream hashes, idempotent re-import, stale marking instead of deletion.
- **Catalog** — one channel per real channel, many streams per channel, permanent UUID + short
  public key used in URLs.
- **Delivery** — deterministic per-device M3U with `url-tvg`, `x-tvg-url` and `tvg-url`; a resolver
  that redirects inside the requested container only, and answers `HEAD` locally.
- **Security** — Argon2id passwords, TOTP second factor, server-side sessions, CSRF on unsafe
  methods, AES-256-GCM at rest for provider URLs, SHA-256 device tokens with rotation/revocation,
  SSRF guard on imports.

## Quick start (local, SQLite)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 32-byte key for encryption at rest
$env:IPTV_ENCRYPTION_KEY = (& .\.venv\Scripts\python.exe -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())")
$env:IPTV_PUBLIC_BASE_URL = "http://127.0.0.1:8000"

# schema + first admin (prints the TOTP secret and otpauth:// URI once)
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m iptvcc.cli create-admin --email admin@local

# serve
.\.venv\Scripts\python.exe -m iptvcc
```

### Use it

```powershell
# login (returns a CSRF token; the session lives in an HttpOnly cookie)
$body = '{"email":"admin@local","password":"<password>","totp":"<code>"}'
$login = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/auth/login `
  -ContentType application/json -Body $body -SessionVariable web
$csrf = $login.csrf_token
$h = @{ 'X-CSRF-Token' = $csrf }

# add a source and import a playlist body
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/sources -WebSession $web -Headers $h `
  -ContentType application/json -Body '{"name":"Demo","url":"http://example.test/list.m3u"}'

$m3u = "#EXTM3U`n#EXTINF:-1 tvg-id=`"bbc1.uk`",BBC One`nhttp://provider.test/1.ts"
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/sources/1/import -WebSession $web -Headers $h `
  -ContentType application/json -Body (@{ m3u = $m3u } | ConvertTo-Json)

# create a device; the token is returned exactly once
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/devices -WebSession $web -Headers $h `
  -ContentType application/json -Body '{"name":"Living Room","kind":"android_tv"}'
```

Then point OwnTV or VLC at `http://127.0.0.1:8000/p/<token>/live.m3u`.

## Layout

```
src/iptvcc/          application package
  config.py          settings (IPTV_* environment)
  db.py              async engine + session
  models.py          SQLAlchemy 2.0 models
  auth.py            sessions, CSRF, login dependency
  passwords.py       Argon2id hashing
  totp.py            RFC 6238 helpers
  secrets.py         AES-256-GCM at rest, URL masking
  m3u.py             parser + deterministic renderer
  matching.py        normalisation + fuzzy scoring
  netguard.py        SSRF guard
  cli.py             create-admin
  api.py             FastAPI app and routes
migrations/          Alembic (initial schema)
scripts/start.sh     container entrypoint: migrate, then serve
tests/               pytest
docs/                architecture, roadmap, decisions
```

## Quality gates

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest -q
```

## Non-goals (by design)

No transcoding, no relay, no media proxying in the default architecture. A 302 response is not a
proxy: it carries no media bytes. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[ADR-0001](docs/decisions/0001-direct-streaming.md).

## License

AGPL-3.0-only. Reused fragments of Dispatcharr (also AGPL-3.0-only) are attributed in `NOTICE`.
