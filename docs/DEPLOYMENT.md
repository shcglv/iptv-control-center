# Deployment plan — Coolify

**Status: executed on 2026-11-27** by the owner's explicit instruction.

The plan below was carried out against `176.123.161.28` through the Coolify API (project
`IPTV Control Center`, application `iptv-api`, database `iptv-db`), with the domain in Coolify and the
DNS record set to DNS-only:

| Item | Value |
|---|---|
| URL | `https://iptv.joxit.ru` (TLS, 89 days at deploy time) |
| Application | Coolify application `piw2ntwkptugw2wmxwqse2yg`, Dockerfile, port 8000 |
| Database | Coolify PostgreSQL 16 resource `slolozwonq1lgtimjbn8vqjl`, internal network only |
| Migrations | `ec324056e4de`, applied by the entrypoint on start |
| Health | `/healthz` → `{"status":"ok","version":"1.7.11","database":"ok"}` |
| Panel preflight | 8/8 ok |
| `smoke_public.py` | 5/5 ok |

Two defects surfaced only at this step and were fixed in 1.7.11: the image could not write to
`data/` (root-owned before the `USER` switch), and there was no way to rotate an admin password
(`cli reset-password` now exists).

## Shape

```
iptv.joxit.ru (A, DNS-only) ──► Traefik (Coolify) ──► iptv-api (Dockerfile, 1 container)
                                                        │
                                                        └──► iptv-db (PostgreSQL 16)
```

- One application container + one managed database. No worker, no Redis, no published port.
- Video never transits the server: playlists point at provider URLs, and the resolver answers 302.
- The probe scheduler runs inside the API process; its DB lease prevents duplicate rounds.

## Resources to create

| Resource | Value |
|---|---|
| Project | new project `iptv` (or an existing project if you prefer) |
| Database | `PostgreSQL 16`, name `iptv-db`, internal only |
| Application | `iptv-api`, build pack **Dockerfile**, branch `main`, port `8000` |
| Domain | `iptv.joxit.ru`, Let's Encrypt via HTTP-01, **proxy off at Cloudflare** |
| Memory limits | app **384 MB**, database **256 MB** (hard total 640 MB; steady ≈220 MB) |
| Healthcheck | `/healthz`, 30 s interval, 20 s start period |
| Backups | Coolify DB backup → daily, retained 7; one copy off-server via `backup-crypt` |
| Monitoring | Uptime Kuma HTTP check on `https://iptv.joxit.ru/healthz`, 60 s |

Fits the current server headroom: ~1.7 GB available, well under the 640 MB budget.

## Environment variables

| Variable | Value | Notes |
|---|---|---|
| `IPTV_ENCRYPTION_KEY` | `openssl rand -base64 32` | **Record it in the password manager.** Losing it makes every stored stream URL unreadable. |
| `IPTV_DATABASE_URL` | `postgresql+psycopg://…` from the DB page | Use the internal URL, not the public one |
| `IPTV_PUBLIC_BASE_URL` | `https://iptv.joxit.ru` | Playlist URLs are built from this |
| `IPTV_DISPLAY_TIMEZONE` | `Europe/Moscow` | Display only; storage is UTC |
| `IPTV_APP_ENV` | `production` | Forces HTTPS assumptions and refuses the dev key |
| `IPTV_TELEGRAM_BOT_TOKEN` | BotFather token, or empty | Long polling needs no inbound port |
| `IPTV_TELEGRAM_OWNER_ID` | your numeric id | First message from it bootstraps the allowlist |
| `IPTV_ALLOW_PRIVATE_NETWORK_TARGETS` | `0` | Only `1` for a LAN provider |
| `IPTV_PUBLIC_RATE_LIMIT_PER_MINUTE` | `120` | Per device token and client address |
| `IPTV_PROBE_MODE` | `passive` | `active` needs an image built with `INSTALL_FFMPEG=true` (+628 MB) |

## Steps (after GO)

1. **Database.** Create `iptv-db`; copy the internal connection URL.
2. **Application.** Create `iptv-api` from the repository, Dockerfile build, port 8000.
3. **Domain.** Set `iptv.joxit.ru` on the application, enable Let's Encrypt (HTTP-01).
4. **DNS.** A record `iptv.joxit.ru → 176.123.161.28`, **proxy disabled**. The resolver path must not
   go through Cloudflare.
5. **Environment.** Paste the table above; deploy.
6. **Watch the log.** The entrypoint waits up to 120 s for PostgreSQL (IPTV_DB_WAIT_SECONDS) before running migrations, so a slow database does not fail the deploy. A first start runs every migration; the image allows 60 s before its health status counts against it. Expected order: `alembic upgrade head` reaching `ec324056e4de`, then
   `Uvicorn running on http://0.0.0.0:8000`, then a `telegram bot started` line if configured.
7. **Preflight inside the container:**
   ```bash
   docker exec <container> python scripts/preflight.py
   ```
   Expected `0 failed`. Do not continue on a failure. The same eight checks are available from the
   panel — Overview → **Система** → `Проверить` — which is the easier option when you have no shell
   at hand. To exercise that path against a local image before deploying:
   ```powershell
   .\.venv\Scripts\python.exe scripts\verify_panel.py
   ```
8. **First admin:**
   ```bash
   docker exec -it <container> python -m iptvcc.cli create-admin --email you@example.com --password '<12+ chars>'
   ```
   Store the TOTP secret immediately.
9. **Verify:** `https://iptv.joxit.ru/healthz` returns `{"status":"ok"}`; the login page renders;
   `/api/v1/scheduler/status` is reachable after login; and the Overview **Система** card shows the
   expected version with `Проверить` reporting `0` failures.
10. **Backups.** Enable the Coolify schedule and confirm one backup exists; verify with
    `pg_restore --list`.
11. **Monitoring.** Add the Kuma check.
12. **Acceptance.** Run [`ACCEPTANCE.md`](ACCEPTANCE.md) with a real provider.

## Rollback

| Situation | Action |
|---|---|
| Migration fails at start | Container exits, old one keeps serving. Read the log, fix forward; do not downgrade a migrated database. |
| Bad release, migration not yet applied | Coolify → redeploy previous image. |
| Bad release, migration applied | Stop the app, restore the pre-deploy backup, redeploy the previous image. |
| Wrong env value | Fix the variable and redeploy; no data impact. |

The database is the only stateful component. `scripts/backup.py` and the Coolify schedule cover it;
the encryption key lives outside the database and must be backed up separately.

## Risk register

| Risk | Mitigation |
|---|---|
| Encryption key lost | Stored in the password manager; without it the catalog must be re-imported |
| Provider credentials leak | URLs encrypted at rest, masked in logs and API; 302 exposes the URL to the player by design |
| Rate limiting too tight for a device | 120/min is generous; raise after identifying the caller |
| One process runs both API and scheduler | Accepted for this scale; the DB lease prevents double rounds if a second replica appears |
| SQLite is not used in production | PostgreSQL from day one |
| Public resolver used as an open redirect | Requires a valid device token; tokens are revocable and rate-limited |

## Explicit non-goals in this deployment

- No Cloudflare proxy for `iptv.joxit.ru`.
- No `docker run` outside Coolify.
- No DNS, TLS or firewall changes beyond the single A record and the Coolify-managed certificate.
- No data migration from anywhere: this is a fresh database.

## GO checklist

The owner approves deployment when all of these are true:

- [ ] The image in the repository builds (CI green, or `docker build` locally).
- [ ] `docs/ACCEPTANCE.md` has been run at least once against a local container.
- [ ] The encryption key has been generated and stored.
- [ ] The Telegram bot token (if any) is ready.
- [ ] The domain `iptv.joxit.ru` is still free.
- [ ] Deployment is approved for the exact resource names above ("GO for iptv-deploy-2026-11"). 
