# ADR-0001 — Smart Redirect is the default; 302 is not a proxy

**Status:** accepted · 2026-11-25

## Context

The product must not carry video. At the same time, players cache playlists for days (OwnTV keeps
its playlist in Room with auto-refresh **off** by default), cannot send custom headers, and provider
tokens expire. A playlist that embeds provider URLs therefore breaks silently after a day, and it
exposes credentials in a file that gets copied around.

## Decision

Playlists contain **our** stable URL per channel (`/p/<token>/s/<key>.<ext>`). The server answers
with `302 Location: <provider url>` chosen at request time, or `503` if no healthy stream of that
container exists. A device may opt into **Pure Direct** later, where the provider URL is emitted
verbatim.

## Consequences

- **Good:** source selection happens per request, so failover works at every playback start; the
  playlist carries no credentials; no expiring tokens in a cached file.
- **Good:** the server still touches zero media bytes — the redirect is tens of bytes.
- **Bad:** the `Location` header exposes the provider URL (and its credentials) to the device.
  This is documented and accepted; it is not confidentiality, it is a smaller leak surface.
- **Bad:** an already-playing stream cannot be switched. The player is connected to the provider.
- **Bad:** a `.ts` endpoint can never answer with an HLS manifest. Players pick their extractor
  from the URL before following the redirect (ExoPlayer then fails with
  `ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED`, mpv loops). The resolver therefore filters by
  container and answers `503` otherwise.
- **Bad:** sources that require a specific `User-Agent`/`Referer` cannot be served this way; they
  are marked incompatible and skipped for profiles that cannot send headers.
