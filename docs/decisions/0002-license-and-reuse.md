# ADR-0002 — License and third-party code reuse

**Status:** accepted (owner decision, 2026-11-25)

## Context

Dispatcharr (AGPL-3.0-only) contains proven implementations of M3U parsing, Xtream access, EPG
matching and per-field channel overrides. Its streaming proxy, however, is the spine of that
project (≈22k LOC with no models, all state in Redis) and is exactly what this project must not
carry. The owner approved selective reuse and plans to publish this project's source.

## Decision

1. This project is licensed **AGPL-3.0-only**.
2. Reuse is **selective**: at most the fragments listed below, each with an attribution header
   naming the upstream path and commit, and an entry in `NOTICE`.
3. No module is imported, forked or vendored wholesale.

| Fragment | Upstream | Status |
|---|---|---|
| EXTINF attribute parsing / entry iteration | `apps/m3u/tasks.py` | design-informed; own implementation |
| Normalisation + regional fuzzy scoring | `apps/channels/epg_matching.py` | design-informed; own implementation |
| Xtream client shapes | `core/xtream_codes.py` | planned |
| Per-field channel override schema | `apps/channels/models.py` (`ChannelOverride`) | planned |
| Stream hash identity | `apps/channels/models.py` (`stream_hash`) | own implementation |

`iptv-org/epg` is **not** used as code: its `LICENSE` says Unlicense while `package.json` says
`UNLICENSED`. If it is ever integrated, it runs as an unmodified external process.

## Consequences

- Any copied fragment forces the whole project to stay AGPL — already chosen.
- `NOTICE` must be updated in the same commit that ports a fragment.
- A `THIRD-PARTY-NOTICES.md` and SBOM are generated per release (P1 CI).
