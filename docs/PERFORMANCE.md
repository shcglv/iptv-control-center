# Performance at the target scale

The product targets 5–15 devices, 2 000–10 000 channels and 5–10 sources. These are measurements at
the upper end of that range, taken with `scripts/bench_scale.py` and profiled with cProfile.

## Method

```powershell
.\.venv\Scripts\python.exe scripts\bench_scale.py --channels 10000 --sources 3 --epg-channels 10000 --programmes 24
```

The script creates a throwaway SQLite database (unless `IPTV_DATABASE_URL` is set), imports a
generated playlist, renders a device playlist three times (median reported), ingests a generated
XMLTV document, maps channels and builds a device guide. Nothing touches real data.

**Caveat:** these runs use SQLite on a Windows development machine with a single process. PostgreSQL
on the target server will behave differently — usually faster for the write-heavy import, and the
per-request read paths are dominated by Python rather than the database. Treat these as a
pessimistic baseline, not as production SLOs.

## Result — 10 000 channels, 30 000 streams, 240 000 programmes

| Operation | Before | After | Notes |
|---|---|---|---|
| Import (cold) | 43.4 s | **7.7 s** | 10 000 channels, 30 000 streams, 3.3 MiB playlist |
| Import (re-run) | 6.9 s | **3.9 s** | 30 000 unchanged streams |
| Playlist render | 3.69 s | **1.46 s** | 1.9 MiB M3U, median of 3 |
| EPG ingest | 52 s (at 3 000 ch) | **43.7 s** | 240 000 programmes, 39 MiB XML |
| EPG mapping | — | **1.25 s** | 10 000 channels |
| Guide build | — | **21.4 s** | 39 MiB XML, 1.5 MiB gzip; cached per profile for 5 min |

At 3 000 channels / 72 000 programmes the same run gives: import 2.1 s, re-run 0.8 s, render 0.36 s,
EPG ingest 13.8 s, guide 4.9 s.

## What was actually slow

1. **One `flush()` per new channel during import.** A playlist creates a channel per unique
   `tvg-id`; flushing inside the loop turned the attach phase into thousands of round trips. Now a
   single flush assigns ids to every new channel.
2. **One `SELECT` per stream to check for an existing link.** Replaced with a chunked preload of
   every link for the streams in this import.
3. **30 000 ORM inserts for streams and links.** New streams are inserted with `insert(Stream)` in
   batches of 5 000 and their ids read back by hash; links are bulk-inserted the same way.
4. **ORM hydration of the whole catalog when rendering a playlist.** The renderer only needs
   stream id, container, staleness, priority and role, so it now reads a flat projection
   (`load_link_views`) and ranks `LinkView` values. This alone halved render time.
5. **One `SELECT` per channel when building a guide**, each returning ORM `Programme` objects.
   Programmes are now fetched for 200 channels at a time as plain columns.
6. **`datetime.fromtimestamp(...).strftime(...)` per timestamp.** Two calls per programme cost ~1 s
   per 48 000 timestamps on Windows; the formatter now builds the string from `time.gmtime`.

## Known costs and how they are contained

| Cost | Containment |
|---|---|
| Playlist render is per request (~1.5 s at 10 000 channels, 1.9 MiB) | Players fetch it at startup and cache it; OwnTV ships with auto-refresh off. A profile-based render cache is the next obvious step if it becomes a problem. |
| Guide build ~21 s at 10 000 channels | Cached on disk per **profile** (not per device) for 5 minutes, so ten devices on one profile cause one render. |
| EPG ingest ~44 s for 240 000 programmes | Runs on the import/schedule path, not on a device request. |
| SQLite single-writer | Production uses PostgreSQL; imports are scheduled rather than interactive. |

## Image variants

Measured with `docker images` after building each variant:

| Build | Size | Why |
|---|---|---|
| default | **367 MB** | no ffmpeg, no pg client |
| `--build-arg INSTALL_PG_CLIENT=true` | **448 MB** | adds `pg_dump`/`pg_restore` (+81 MB) |
| `--build-arg INSTALL_FFMPEG=true` | **977 MB** | adds ffmpeg for active probing (+610 MB) |

The default image backs up SQLite with the online API but cannot dump PostgreSQL: use Coolify's
database backups, or build with `INSTALL_PG_CLIENT=true`, or run `scripts/backup.py` from a host that
has `pg_dump` and point `IPTV_PG_DUMP` at it.

## Probe modes: what active mode costs

`IPTV_PROBE_MODE=active` adds ffprobe to the image. Measured on this machine:

| Image | Size |
|---|---|
| default (`INSTALL_FFMPEG=false`) | **349 MB** |
| `--build-arg INSTALL_FFMPEG=true` | **977 MB** |

That is **+628 MB**, not the "+70 MB" one would guess: Debian's `ffmpeg` package pulls a large
dependency tree (librsvg, cairo, fonts, and so on) even with `--no-install-recommends`. The binary
itself works and the process still runs as UID 10001.

Recommendation: keep passive mode on the server. Enable active mode only if "does it actually
decode" is worth ~0.6 GB of image and ~1 CPU-second per probed stream. A slim alternative would be a
statically linked ffprobe build, which was not used because it means trusting a third-party binary.

## Reproduce

```powershell
# the headline numbers
.\.venv\Scripts\python.exe scripts\bench_scale.py --channels 10000 --sources 3 --epg-channels 10000 --programmes 24

# smaller, faster loop while changing code
.\.venv\Scripts\python.exe scripts\bench_scale.py --channels 3000 --sources 2 --epg-channels 3000 --programmes 24
```
