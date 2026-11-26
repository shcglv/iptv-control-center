# Release 1.6.5

Verified end to end as an artifact: backend on both databases, browser, container, both backup drills, a real public playlist and guide, a real decoder through the 302, and the panel's own preflight inside the image. **It has not been deployed anywhere yet** — that is step 10.

- Version: `1.6.5`
- Image: built from this commit, 367 MB default
- Evidence: [`docs/VERIFICATION.md`](docs/VERIFICATION.md) · status:
  [`docs/STATUS.md`](docs/STATUS.md)

## What it does

A self-hosted control plane for IPTV. It imports provider playlists, keeps one channel catalog,
serves a per-device M3U and XMLTV guide, picks the best source per channel from health data, and
answers playback with a `302` straight to the provider. **It never carries video.**

Screens: Overview (with a system check), Channels (with per-channel diagnostics), Quality, Discovery,
Playlists (with group visibility), Catalog (with a duplicate review queue), Sources, Devices. Telegram
bot for status and alerts. Backups, preflight and a runbook.

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

6. **Deploy.** The log must show `alembic upgrade head` reaching `198a8944b683`, then Uvicorn, then
   `telegram bot started` if a token is set.
7. **Preflight inside the container**: `docker exec <container> python scripts/preflight.py` →
   expect `0 failed`. Do not continue on a failure.
8. **First admin**: `docker exec -it <container> python -m iptvcc.cli create-admin --email … --password '…'`.
   The TOTP secret is printed once.
9. **Backups**: enable the Coolify database schedule; add an off-server copy.
10. **Acceptance**: run [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) with a real provider and one TV.

## Verify it is alive

```bash
curl -fsS https://iptv.joxit.ru/healthz          # {"status":"ok"}
```

Then log in, create a source, import a playlist, create a device, and open the M3U in VLC.

## Rollback

- Migration not yet applied → redeploy the previous image.
- Migration applied → stop the app, restore the pre-deploy backup, redeploy the previous image.
- Wrong env value → fix the variable and redeploy; no data impact.

Details and the risk register: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Included, verified

Direct streaming · M3U import with a provider corpus · channel catalog and identity catalog with
take-down blocks · playlist profiles (visibility, order, drag-and-drop, source pins) · devices with
hashed tokens and rotation · XMLTV ingest with a corpus · per-device guide cached per profile ·
health probes, composite score, anti-flapping, autonomous scheduler · active probe mode (opt-in
image) · discovery with review queue · Telegram bot with dedupe · nine UI screens · SSRF suite ·
rate limiting · request ids and channel diagnosis · SQLite **and** PostgreSQL · backups with drilled
restores · preflight · runbook.

## Not included

- Client Compatibility Engine, fuzzy match review queue, smart-TV guide cap, category editor in the
  playlist studio, authenticated forum sessions, scheduled re-crawls, provider slot budget, Telegram
  widgets. Each is listed in [`docs/STATUS.md`](docs/STATUS.md).
- A deployed instance, a real provider run, a real Telegram delivery, and a CI execution: all three
  need owner actions (credentials, GO, a TV).
