# Acceptance test with a real provider

The automated suite proves the software behaves correctly with fixtures: 161 backend tests, four
browser tests, three container tests, and a drilled restore. It cannot prove that a real provider's
playlist parses, that a real TV plays the result, or that Telegram actually reaches you.

This is the manual pass that closes that gap. Budget 30–40 minutes.

## What you need

| Item | Notes |
|---|---|
| One real provider | M3U URL, or Xtream host + username + password. Use the small subscription if you have several. |
| One TV or a desktop player | OwnTV 5.2.2 on Android TV, or VLC on the same network as the server. |
| Telegram bot token | Optional but recommended: BotFather → `/newbot`. |
| A working instance | Local Docker or the deployed URL from [`DEPLOYMENT.md`](DEPLOYMENT.md). |

Write down results as you go; the report template is at the end.

## 0. Start clean

```bash
docker run -d --name iptv-acceptance -p 8000:8000 \
  -e IPTV_ENCRYPTION_KEY="$(openssl rand -base64 32)" \
  -e IPTV_DATABASE_URL="sqlite+aiosqlite:////data/iptv.db" \
  -e IPTV_PUBLIC_BASE_URL="http://<host-lan-ip>:8000" \
  -v iptv-acceptance:/data \
  iptv-control-center:local
```

Use the **LAN IP**, not `localhost` — the TV must be able to reach it.

```bash
docker exec iptv-acceptance python scripts/preflight.py
```

Expected: `0 failed`. A failure here is a bug report, not something to work around.

## 1. First login

- [ ] Open `http://<host>:8000`, the login page appears.
- [ ] Create the admin: `docker exec -it iptv-acceptance python -m iptvcc.cli create-admin --email you@example.com --password '<12+ chars>'`.
- [ ] The TOTP secret is printed **once**; add it to your authenticator.
- [ ] Log in with password + code. The Overview renders.

**Pass:** you are inside within one minute. **Fail:** anything else — note the exact error.

## 2. Import the real provider

- [ ] Sources → add a source, paste the provider's M3U URL → save.
- [ ] Open the source, paste the playlist body (or use the URL import), run it.
- [ ] Import finishes; note the counters: parsed / created / updated / stale / channels.

| Check | Pass looks like |
|---|---|
| Channel count | Roughly matches what the provider advertises (a few dozen duplicates is normal) |
| Groups | Channels land in groups, not all in one bucket |
| Re-import | Running the same playlist again reports `updated`/`unchanged`, channels do not double |
| Time | Under ~30 s for 10 000 channels (see `docs/PERFORMANCE.md`) |

- [ ] Channels screen shows names, numbers, groups.
- [ ] Open one channel → its ranked sources appear with scores.

## 3. Playlist in VLC

- [ ] Devices → create `VLC`, copy the M3U URL.
- [ ] VLC → Open Network Stream → paste → play.
- [ ] Zap through 10 channels, including at least one sports and one news channel.

| Check | Pass looks like |
|---|---|
| Startup | Playlist loads in a few seconds |
| Zapping | Each channel starts; note any that do not |
| Container | No channel shows a manifest/parsing error (a `.ts` URL must not receive an HLS manifest) |
| Stability | 5 minutes on one channel without stalls |

Note the time to first frame for the slowest channel.

## 4. Playlist in OwnTV

- [ ] Add the playlist URL in OwnTV (Playlist → Add → URL).
- [ ] Add the EPG URL from the Devices screen (`/p/<token>/epg.xml`).
- [ ] Let OwnTV refresh; it caches the playlist with auto-refresh **off**.

| Check | Pass looks like |
|---|---|
| Channel list | Names and groups visible; logos optional |
| Playback | Channels start; the same ones that worked in VLC work here |
| Guide | Programme names and times appear for the channels that have EPG |
| Zapping | No `ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED` in logcat |

If a channel fails only in OwnTV, note its container (`.ts` vs `.m3u8`) — that is the known
ExoPlayer constraint.

## 5. Failover with two sources (recommended)

Only if you have a second provider or a second playlist.

- [ ] Import the second playlist into a second source; channels should merge by `tvg-id`/name.
- [ ] Open one channel, confirm two sources are ranked.
- [ ] Catalog → blocklist → kind **Домен источника**, value = second provider's host → block.
- [ ] Play the channel again: it must play from the remaining source.
- [ ] Remove the block; both sources return.

**Pass:** playback survives the block without touching the player.

## 6. Telegram

- [ ] Set `IPTV_TELEGRAM_BOT_TOKEN` and `IPTV_TELEGRAM_OWNER_ID`, restart the container.
- [ ] Message the bot `/start` → it answers with counters.
- [ ] `/status`, `/sources`, `/problems` → sensible answers, no stack traces.
- [ ] Trigger a problem: block a provider domain (as in §5) and wait ≤ 6 minutes.
- [ ] You receive one alert. Unblock; a recovery message arrives on the next scan.

## 7. Restart and persistence

- [ ] `docker restart iptv-acceptance`, wait for `/healthz`.
- [ ] Log in again: channels, sources, devices and blocks are all still there.
- [ ] The TV playlist still plays.

## 8. Report back

Paste this, filled in:

```
Version:            (Overview footer or /api/v1/health/... )
Deployment:         docker local | coolify
Channels imported:  ___   Groups: ___
VLC:                works __/10 channels, slowest first frame ___ s
OwnTV:              works __/10 channels, EPG: yes/no, errors: ______
Failover:           passed / failed / not tested
Telegram:           passed / failed (which command)
Restart:            passed / failed
Problems noticed:   ______
Logs:               docker logs iptv-acceptance 2>&1 | tail -100
```

## What counts as a failed acceptance

Any of these is a bug, not a configuration issue:

- import returns fewer channels than the playlist contains with no `stale` explanation,
- a channel resolves to a URL of the wrong container,
- the resolver returns 503 for a channel that has a healthy source,
- the guide shows programmes on the wrong channel,
- Telegram sends nothing while `/problems` lists a problem,
- data disappears after a restart.

Anything else — a provider being down, a wrong password, an IPTV box with a broken player — is not
this product's failure, but still worth reporting.
