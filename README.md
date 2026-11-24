# IPTV Control Center

Self-hosted control plane for IPTV: sources, a single channel catalog, EPG, quality scoring and
per-device playlists. **Video never transits the server** — playlists point at provider URLs, and the
server's only role in playback is an optional HTTP 302 that picks the best source at request time.

> **Status: `0.1.0` — vertical slice.** Working: M3U import → channel catalog → per-device playlist →
> 302 source resolution. Not yet: admin login (TOTP), EPG, quality engine, discovery, Telegram, web UI.
> See `docs/ROADMAP.md` for the phase plan.

## What it does today

```
provider M3U ──► import ──► streams ──► matching ──► channels
                                                       │
device /p/<token>/live.m3u ◄── render (deterministic) ─┘
device /p/<token>/s/<key>.ts ──► 302 Location: provider URL ──► player ◄── video (direct)
```

- **Import** — streamed M3U/M3U8 parsing, attribute handling (`tvg-id`, `tvg-name`, `tvg-logo`,
  `group-title`, `#EXTGRP`, `#EXTVLCOPT`), stable stream hashes, idempotent re-import.
- **Catalog** — one channel per real channel, many streams per channel, permanent UUID + short public key.
- **Delivery** — deterministic per-device M3U with all three EPG attributes, and a resolver that
  returns `302` to the selected provider URL without touching media bytes.
- **Security** — AES-256-GCM at rest for provider URLs, opaque device tokens stored as SHA-256,
  instant revocation, admin gate.

## Quick start (local, SQLite)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$env:IPTV_ENCRYPTION_KEY = (python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())")
.\.venv\Scripts\python.exe -m iptvcc        # http://127.0.0.1:8000
```

Then:

```powershell
# 1. create a source
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/sources `
  -Headers @{ 'X-Admin-Token' = 'dev-admin-key-change-me' } `
  -ContentType application/json `
  -Body '{"name":"Demo","url":"http://example.test/list.m3u"}'

# 2. import playlist text (file content)
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/sources/1/import `
  -Headers @{ 'X-Admin-Token' = 'dev-admin-key-change-me' } `
  -ContentType application/json `
  -Body (@{ m3u = "#EXTM3U`n#EXTINF:-1 tvg-id=`"bbc1.uk`" tvg-logo=`"http://l/1.png`" group-title=`"News`",BBC One`nhttp://prov/1.ts" } | ConvertTo-Json)

# 3. create a device, get its token once
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/devices `
  -Headers @{ 'X-Admin-Token' = 'dev-admin-key-change-me' } `
  -ContentType application/json -Body '{"name":"Living Room","kind":"android_tv"}'

# 4. fetch the playlist
Invoke-WebRequest http://127.0.0.1:8000/p/<token>/live.m3u
```

## Layout

```
src/iptvcc/          application package
  config.py          settings (env, .env)
  db.py              async engine + session
  models.py          SQLAlchemy 2.0 models
  secrets.py         AES-256-GCM at-rest encryption
  m3u.py             parser + deterministic renderer
  matching.py        normalisation + channel matching
  services/          import, delivery, catalog
  api.py             FastAPI app and routes
tests/               pytest (parser, matching, API, resolver)
docs/                architecture, roadmap, decisions
```

## Non-goals (by design)

No transcoding, no relay, no media proxying in the default architecture. A 302 response is not a
proxy: it carries no media bytes. See `docs/ARCHITECTURE.md`.

## License

AGPL-3.0-only. Reused fragments of Dispatcharr (also AGPL-3.0-only) are attributed in `NOTICE`.
