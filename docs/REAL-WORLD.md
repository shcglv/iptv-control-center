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

## A real decoder consumed the delivery chain

VLC could not be driven headlessly (see below), but **ffmpeg is the demuxer and decoder underneath
most players**, and it is scriptable. `scripts/verify_decoder.py` runs the whole chain inside Docker,
so no host firewall is involved:

```
ffmpeg (container) → /p/<token>/s/<key>.m3u8 → 302 → provider HLS → decoded frames
```

```
resolver: http://iptv-decode-app:8000/p/ipt_…/s/bk9cwv35cy2j.m3u8
host-side resolver status: 302 https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8
decoding through the resolver with ffmpeg
ffmpeg exit: 0
server saw 2 resolver redirect(s)
RESULT: DECODER CHECK PASSED (ffmpeg decoded through our 302)
```

`ffmpeg -i <our resolver URL> -t 3 -f null -` exited **0** with no error output, and the server log
confirms the redirect it followed. This is the closest thing to a player that can be automated here:
a real media stack resolved our token, followed our 302 and decoded the provider's stream.

It still is not VLC or OwnTV — container quirks and player-specific header behaviour remain
unproven, and `ACCEPTANCE.md` on a real television stays the final word.

## The player attempt that failed

VLC 3.x is installed on the development machine, so the obvious step was to play our playlist
through it. It could not be driven headlessly:

- `vlc --intf dummy --vout dummy --aout dummy --play-and-exit --run-time 5 <url>` returns
  `0xC0000005` (access violation) on exit, so the exit code says nothing about playback.
- With `--file-logging` and a remote HLS URL the same invocation does not terminate within three
  minutes, even with `--run-time`.
- Against our playlist URL, the server log showed **zero** requests: VLC never fetched it.

Two server-side hypotheses were eliminated: VLC's single-instance mode (`--no-one-instance`) and
Python stdout buffering in the server child (`PYTHONUNBUFFERED=1`). Neither changed the result.

Recorded because "we tried and it did not work" is more useful to the next person than silence.

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
- **The EPG mapping is weak on this pairing.** 13 of 1 028 channels matched a UK guide; the guide
  was parsed correctly, but identity matching needs the owner's provider, whose `tvg-id`s are the
  ones its guide will use.
- **Single run.** Public streams rotate; a later run may find different endpoints dead.

Two of the twelve entries were skipped at import because they were not `http(s)`. That is the
documented behaviour from the provider corpus, now observed on real data.

## EPG from a real public guide

Fetched the Freeview EPG (`https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml`,
20.9 MiB) and ingested it alongside the public playlist:

```
epg ingest: {'channels': 287, 'programmes': 48814, 'skipped': 0, 'pruned': 0, 'mapped': 13}
catalog: 1028 channels, 48814 programmes, mapped 13
  programme: 'Good News on GREAT!' at 1796169360
```

Reading 48 814 programmes from a 21 MiB document with zero skipped records is the same code path the
XMLTV corpus exercises, now on a real guide. Only 13 of 1 028 channels mapped because a UK Freeview
guide and a global news playlist use different channel ids — that is an identity-matching limit, not
a parser failure. Three other public guide URLs returned HTTP errors, which is why the check treats
any one working source as success.

## Active probing against real streams

`IPTV_PROBE_MODE=active` with the ffmpeg image, current source mounted, real URLs:

```
ffprobe binary: /usr/bin/ffprobe
test-streams.mux.dev        ok          1280x720  h264  streams=10
2gblive.akamaized.net       no streams  -         -     streams=0
```

The first line is the positive case: ffprobe read a real HLS stream and reported its real format.

The second line found a genuine gap. That public entry still answers HTTP, and its manifest still
parses — but its variant playlists return `404`, so ffprobe exits **0** with zero streams. Active
mode recorded that as healthy, because it only checked ffprobe's exit code. A stream that nothing
can decode is not healthy, so `MediaInfo` now carries `stream_count`, and a zero-stream inspection
fails with `ffprobe_no_streams`. Covered by a new test.

This is exactly the class of bug that fixtures cannot produce: the manifest was valid, the HTTP
status was 200, and the only clue was an empty `streams` array.
