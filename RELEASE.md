# Release 1.7.11

Verified as an artifact: 271 backend tests on SQLite **and** PostgreSQL 16, the browser suite, the
container acceptance run, an install drill, an upgrade drill, a backup and a restore drill, a real
public playlist and guide, a real decoder through the `302`, the panel's own preflight inside the
image, and a security audit with six findings fixed. **It has not been deployed anywhere yet** — that
is step 10.

- Version: `1.7.11`
- Image: one Dockerfile, `python:3.12-slim` base, roughly 370 MB by default
- Evidence: [`docs/VERIFICATION.md`](docs/VERIFICATION.md) · status:
  [`docs/STATUS.md`](docs/STATUS.md) · security: [`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md)

## What it does

A self-hosted control plane for IPTV. It imports provider playlists, keeps one channel catalog,
serves a per-device M3U and XMLTV guide, picks the best source per channel from health data, and
answers playback with a `302` straight to the provider. **It never carries video.**

Screens: Overview (with a system check), Channels (per-channel diagnostics), Quality, Discovery,
Playlists (group visibility, ordering, drag-and-drop), Catalog (identity catalog, take-down blocks,
duplicate review), Sources, Devices, Login. Telegram bot for status and alerts. Backups, preflight,
runbook.

## Install — ten steps

1. **PostgreSQL 16** in Coolify, name `iptv-db`. Copy the internal connection URL.
2. **Application** from this repository: build pack `Dockerfile`, port `8000`.
3. **Domain** `iptv.joxit.ru`, Let's Encrypt over HTTP-01.
4. **DNS**: one A record to your server, **proxy disabled**.
5. **Environment**:

   | Variable | Value |
   |---|---|
   | `IPTV_ENCRYPTION_KEY` | `openssl rand -base64 32` — store it in the password manager |
   | `IPTV_DATABASE_URL` | `postgresql+psycopg://…` from step 1 |
   | `IPTV_PUBLIC_BASE_URL` | `https://iptv.joxit.ru` |
   | `IPTV_APP_ENV` | `production` |
   | `IPTV_DISPLAY_TIMEZONE` | `Europe/Moscow` |
   | `IPTV_TELEGRAM_BOT_TOKEN` | optional |
   | `IPTV_TELEGRAM_OWNER_ID` | optional, your numeric id |

   Optional and safe to leave alone: `IPTV_EPG_MAX_BYTES` (5 MB), `IPTV_MAX_REQUEST_BYTES` (64 MB),
   `IPTV_PUBLIC_RATE_LIMIT_PER_MINUTE` (120). The API schema stays off in production; set
   `IPTV_ENABLE_DOCS=true` only when you genuinely need it.

6. **Deploy.** The log must show the Alembic upgrade reaching `ec324056e4de`, then
   `Uvicorn running`, then `telegram bot started` if a token is set.
7. **Check it is wired correctly** — either way, both run the same eight checks:
   ```bash
   docker exec <container> python scripts/preflight.py     # expect 0 failed
   ```
   or, with no shell at all: log in → Overview → **Система** → `Проверить`. Do not continue on a
   failure.
8. **First admin**:
   `docker exec -it <container> python -m iptvcc.cli create-admin --email … --password '…'`.
   The TOTP secret is printed once.
9. **Backups**: enable the Coolify database schedule; add an off-server copy. The dump format is
   verified restorable — see `scripts/verify_restore.py`.
10. **Acceptance**: run [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) with a real provider and one TV.

## Reproduce the verification

All drills in one command, with Docker and network requirements detected and skipped when absent:

```powershell
.\.venv\Scripts\python.exe scripts\verify_all.py
```

## Verify it is alive

From any machine, without credentials:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_public.py --base https://iptv.joxit.ru
```

Five checks: health, login page, a built asset, security headers, and the TLS certificate's remaining
life. It exits non-zero and names the failure if something is wrong.

```bash
curl -fsS https://iptv.joxit.ru/healthz     # {"status":"ok", …}
```

Then log in, create a source, import a playlist, create a device, and open the M3U in VLC.

## Rollback

- Migration not yet applied → redeploy the previous image.
- Migration applied → stop the app, restore the pre-deploy backup, redeploy the previous image.
- Wrong env value → fix the variable and redeploy; no data impact.

The upgrade path itself is drilled: `scripts/verify_upgrade.py` starts at `198a8944b683`, writes data
through the real import path, upgrades to head, and checks that the rows survived and the playlist is
still served.

## Included, verified

Direct streaming with the container guard · M3U import with a provider corpus and a linear parser ·
channel catalog and identity catalog with take-down blocks · duplicate detection with a review queue
and a merge that moves links, overrides and EPG mapping · playlist profiles with visibility, ordering,
drag-and-drop and source pins · **group (category) visibility** · **client compatibility** per device
family (TS/HLS/DASH rules, per-family guide caps) · **smart-TV guide cap** (7→3→1-day windows, never
truncated XML) · devices with hashed tokens and rotation · XMLTV ingest with a corpus and dictionary
hardening · per-device guide cached per profile · health probes, composite score, anti-flapping,
autonomous scheduler · active probe mode (opt-in image) · discovery with a review queue · Telegram bot
with dedupe and a hardened poller · nine UI screens, usable at phone width · SSRF suite · rate
limiting · request ids and channel diagnosis · browser-hardening headers · `nosniff`, `DENY`, HSTS on
HTTPS · SQLite **and** PostgreSQL · backups **and** drilled restores · preflight, upgrade drill,
smoke check, system panel · runbook.

## Not included

- Manual channel split, per-field overrides, the two-device studio impact panel and in-editor
  preview, authenticated forum sessions, scheduled discovery re-crawls, per-source provider slot
  budget, Telegram widgets, SBOM and container image scanning. Each is listed in
  [`docs/STATUS.md`](docs/STATUS.md).
- A deployed instance, a real provider run, a real Telegram delivery, a real player on a television,
  and a CI execution: all need owner actions — credentials, a GO, and a TV.
