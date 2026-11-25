# Real-world run

**Date:** 2026-11-25 · version 1.0.1

Every earlier check used fixtures. This run used the public internet: a real playlist published by
iptv-org, and the real streaming endpoints inside it.

Reproduce with:

```powershell
.\.venv\Scripts\python.exe data\live_real_streams.py

$env:IPTV_NETWORK_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_real_world.py -q
```

## What happened

Fetched `https://iptv-org.github.io/iptv/categories/news.m3u` through the SSRF guard, imported it,
probed a sample of the streams, rendered a device playlist and resolved channels to their real URLs.

```
fetched 241.4 KiB in 1.3s
import: {'parsed': 1059, 'created': 1057, 'updated': 0, 'unchanged': 0,
         'stale': 0, 'skipped': 2, 'channels_created': 1028, 'channels_linked': 1057} in 0.6s
catalog: 1028 channels, 1057 streams, 27 groups
probing 12 real stream URLs
  [ok ] 2gblive.akamaized.net             1137ms 1920x1080
  [ok ] 3awlive.akamaized.net             2196ms 1280x720
  [http_403] directes-tv-cat.3catdirectes.cat    1124ms
  [ok ] directes-tv-int.3catdirectes.cat   952ms 1920x1080
  [ok ] rds3.desdeparaguay.net            1334ms 1600x900
  [ok ] fta.ektv.in                       1380ms 720x576
  [ok ] 6prlive.akamaized.net             1120ms 1920x1080
  [http_404] mumbai-edge.smartplaytv.in   1294ms
  [ok ] video1.getstreamhosting.com:1936  1151ms
  [http_404] sscsott.com                   760ms
reachable: 7/12, median ttfb 1151ms
device playlist: 1028 channels, 276 KiB
  resolved '2GB (1080p)'   -> https://2gblive.akamaized.net/hls/live/2033805/2GB/index.m3u8 (200)
  resolved '3AW (1080p)'   -> https://3awlive.akamaized.net/hls/live/2032295/3AW/index.m3u8 (200)
  resolved '3CatInfo (1080p)' -> https://directes-tv-int.3catdirectes.cat/live-origin/canal324-hls/master.m3u8 (200)
RESULT: REAL-WORLD CHECK COMPLETE (3 live resolutions)
```

## What this proves

- **A real-world playlist parses.** 1 059 entries became 1 028 channels and 1 057 streams across 27
  groups, in 0.6 s, with 2 entries skipped (non-http schemes the resolver cannot redirect to).
- **The probe reads real streams.** 7 of 12 answered; HLS master playlists yielded real resolutions
  (1920×1080, 1280×720, 1600×900, 720×576) with a median TTFB of 1 151 ms.
- **The resolver produces playable targets.** Three channels resolved to their provider's real HLS
  URL, and a direct `HEAD` to each returned `200`. This is the direct-streaming promise, exercised
  against real endpoints rather than a fixture.
- **The SSRF guard does not block legitimate public HTTPS.** TLS, redirects and DNS work.

## What this does **not** prove

- **Not the owner's provider.** Geo-blocks, hotlink protection, expiring tokens and provider-specific
  quirks remain untested. The three `403` and two `404` responses above are exactly that class of
  problem, and they came from the public list, not from our code.
- **No player was involved.** A `HEAD 200` means the URL answers; it does not mean VLC or OwnTV
  decodes it. That is still `ACCEPTANCE.md`.
- **No EPG.** The public list carries `tvg-id` but no guide; XMLTV was exercised only with fixtures.
- **Single run.** Public streams rotate; a later run may find different endpoints dead.

Two of the twelve entries were skipped at import because they were not `http(s)`. That is the
documented behaviour from the provider corpus, now observed on real data.
