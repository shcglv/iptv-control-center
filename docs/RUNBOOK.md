# Runbook

Operational procedures for IPTV Control Center. Everything here is written to be executed by the
owner, on the Coolify server, without reading the source.

## 1. Shape of the deployment

```
Internet ──► Traefik (Coolify) ──► iptv-api (this image, one container)
                                      │
                                      └──► iptv-db (PostgreSQL 16, Coolify Database)
```

One container runs migrations at start (`scripts/start.sh`) and then serves the API, the SPA, the
playlists and the 302 resolver. Video never passes through it: a player that resolves a channel is
redirected straight to the provider.

**No inbound ports are published.** Only Traefik reaches the container, on the internal network.

## 2. First install

1. Coolify → project → **New Database → PostgreSQL 16**, name `iptv-db`. Note the internal
   connection URL and the generated password.
2. **New Application → Dockerfile**, repository and branch, build pack `Dockerfile`.
3. Domain `iptv.joxit.ru`, HTTPS via Let's Encrypt (HTTP-01), **DNS-only** at Cloudflare — the
   server must be reachable directly, and proxying adds nothing but failure modes.
4. Environment variables (Production):

| Variable | Value | Notes |
|---|---|---|
| `IPTV_ENCRYPTION_KEY` | base64 of 32 random bytes | Encrypts stream URLs at rest. **Back it up separately** — without it the database is useless. |
| `IPTV_DATABASE_URL` | `postgresql+psycopg://…` | From the Coolify database page |
| `IPTV_PUBLIC_BASE_URL` | `https://iptv.joxit.ru` | Used to build playlist URLs |
| `IPTV_DISPLAY_TIMEZONE` | `Europe/Moscow` | Display only; storage is UTC |
| `IPTV_TELEGRAM_BOT_TOKEN` | optional | Empty disables the bot |
| `IPTV_TELEGRAM_OWNER_ID` | optional | Your numeric Telegram id |
| `IPTV_ALLOW_PRIVATE_NETWORK_TARGETS` | `0` | Only `1` for a LAN provider, and understand it |

5. Deploy. Watch the log: it must show the Alembic upgrade reaching `head`, then `Uvicorn running`.
6. Create the first admin, inside the container:

```bash
docker exec -it <container> python -m iptvcc.cli create-admin \
  --email owner@example.com --password '<12+ chars>'
```

The command prints the TOTP secret **once**. Scan it immediately; there is no recovery path except
another `create-admin`.

7. Open `https://iptv.joxit.ru`, log in, add a source, import a playlist, create a device, and point
   OwnTV or VLC at the playlist URL.

## 3. Backup

The database is the only stateful thing in the app container; playlists, guides and the UI are
regenerated.

```bash
# inside the container, writes backups/iptv-<timestamp>.dump (custom format)
docker exec <container> python scripts/backup.py --out /data/backup.dump

# or against the database directly from any host with pg_dump
pg_dump --format=custom --no-owner --file=iptv.dump "$IPTV_DATABASE_URL"
```

Schedule it daily, keep 7 daily + 4 weekly, and **store one copy off the server** (the `backup-crypt`
restic repository is the established place). A backup that only exists on the same disk is not a
backup.

Verify a backup without restoring it:

```bash
docker exec <container> pg_restore --list /data/backup.dump | head
```

For SQLite deployments (development, small installs) `scripts/backup.py` uses the online backup API
and prints a row count per table plus `PRAGMA integrity_check`.

## 4. Restore

Decide first whether you are restoring **into an empty database** (new server, disaster) or
**replacing a live one** (bad data). The second is the dangerous one.

```bash
# 1. stop the application so nothing writes
#    Coolify → Application → Stop
# 2. restore into the database
docker exec -i <db-container> pg_restore --clean --if-exists --no-owner \
  --dbname=postgres /tmp/iptv.dump
# 3. start the application and check /healthz
```

The drill for this project is automated: `scripts/backup.py` then a byte copy over a damaged SQLite
file, followed by an API read. It has been executed end to end; a restore is not considered verified
until the API answers with the expected channel count.

After restoring, log in and check: channel count, sources with `stream_count`, the scheduler status,
and one playlist in a player.

## 5. Upgrade

1. Take a backup (see §3). Migrations are forward-only.
2. Coolify → Application → **Deploy** the new image.
3. `scripts/start.sh` runs `alembic upgrade head` before starting the server. If the migration fails,
   the container exits and the old one keeps serving — check the log before retrying.
4. Verify: `/healthz`, the login page, and `GET /api/v1/scheduler/status` for a fresh tick.

Downgrade is not supported by policy: roll back to the previous image only if the migration did not
run, otherwise restore the pre-upgrade backup.

## 6. Secrets

| Secret | Rotation | Consequence |
|---|---|---|
| `IPTV_ENCRYPTION_KEY` | Not rotatable in place | Rotating makes every stored stream URL undecryptable. Re-import every source after rotating. |
| Admin password | Recreate the user with `create-admin` and delete the old row | Active sessions keep working until they expire (12 h) |
| Device tokens | UI → Devices → **Ротация** | Old playlist URL stops working immediately |
| Telegram token | BotFather → revoke, set a new one, restart | None |
| Database password | Coolify → Database → rotate, update `IPTV_DATABASE_URL` | Brief downtime |

## 7. Monitoring

| Signal | Where | Meaning |
|---|---|---|
| `/healthz` | HTTP, unauthenticated | Process is up; does not touch the database |
| `GET /api/v1/scheduler/status` | admin API | `healthy: false` means no probe round within `max(3×tick, 300 s)` |
| Telegram `/problems` | bot | Aggregated: source errors, expiring subscriptions, stalled scheduler, channels without EPG |
| Telegram alerts | push | Sent once per problem per 6 h, plus one recovery message |
| Container log | Coolify | Migrations, probe errors, Telegram poll failures |

For an external check, point Uptime Kuma at `https://iptv.joxit.ru/healthz` with a 60 s interval.

## 8. Incident playbooks

**A provider stopped working.** Sources screen shows `last_status=error`. Open the source, re-import;
if credentials expired, the import reports the provider's HTTP error. Channels keep their other
sources; the resolver falls back automatically.

**Every channel is dead.** Check the scheduler status first: if `healthy: false`, probes are not
running and the catalog is stale. Restart the container and watch the log for the lease.

**The guide is empty in the player.** XMLTV needs `tvg-id` on channels and matching `<channel id>` in
the guide. The EPG screen shows mapped vs unmapped. A guide is cached per profile for 5 minutes, so a
freshly mapped channel can take five minutes to appear.

**A player gets 429.** The public endpoints allow 120 requests/minute per token and client address.
That is generous for a player; a stick hammering the resolver usually means a misconfigured client.
Raise `IPTV_PUBLIC_RATE_LIMIT_PER_MINUTE` only after identifying the caller.

**Active probing.** `IPTV_PROBE_MODE=active` additionally runs ffprobe and proves decodability, but
the binary is not in the default image: build with `--build-arg INSTALL_FFMPEG=true`. Measured cost
is **+628 MB** of image (349 → 977 MB), so it is off by default. Concurrency is capped by
`IPTV_PROBE_CONCURRENCY` (default 2) — do not raise it on a 2-vCPU host.

**Disk is full.** Guides live in the artifacts directory and are regenerated; old `.xml`/`.xml.gz`
files are safe to delete. Check `docker system df` for old images.

**You lost the encryption key.** There is no recovery. Re-create sources and re-import.

## 9. Do not

- Run `docker run` for this app outside Coolify. It is a Coolify application; the exception process in
  `COOLIFY-DEPLOYMENT-POLICY.md` applies.
- Delete the database volume to "start clean" without a backup.
- Disable authentication or TLS verification to make a test pass.
- Rotate `IPTV_ENCRYPTION_KEY` "for hygiene" — it destroys the catalog.
- Point the domain through the Cloudflare proxy for the resolver path; direct DNS is deliberate.
