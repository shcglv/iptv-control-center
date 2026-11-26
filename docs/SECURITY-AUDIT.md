# Security audit — 1.0.1

**Date:** 2026-11-25
**Scope:** the Python runtime and development environment, and the source tree.
**Not covered:** OS packages inside the container image, a SBOM, dynamic scanning, and anything
that requires a running deployment.

Two tools, both now wired into CI (the `security` job, plus a weekly schedule so a new advisory in a
pinned dependency surfaces without a push).

## Dependency advisories

```
pip-audit --progress-spinner off
No known vulnerabilities found
```

`pip-audit` checks every installed distribution against the GitHub Advisory Database and PyPI's
advisory feed. At the time of the run the environment had 52 packages, including both database
drivers and the dev tooling.

Caveat worth stating: this is a point-in-time result. A dependency with no advisory today can have
one tomorrow, which is why the job also runs on a schedule.

## Static analysis

```
bandit -q -c pyproject.toml -r src/iptvcc
No issues identified.
```

Bandit scans 5 900 lines of application code. Its first pass produced five findings; each was
reviewed rather than suppressed wholesale:

| Finding | Verdict |
|---|---|
| B608 — SQL built from a table name (`backup.py`) | **Fixed.** Table names come from the SQLite file, which is data. They are now validated against `[A-Za-z_][A-Za-z0-9_]{0,62}` before use, so the interpolation cannot be influenced by a crafted database. |
| B603 — `subprocess` call for `pg_dump` | Accepted. Fixed binary, argument list, no shell; the database URL is operator configuration. Documented with `# nosec B603` at the call site. |
| B404 — `import subprocess` (two modules) | Accepted project-wide in `[tool.bandit].skips`: both call sites are the previous row and ffprobe. |
| B105 — "hardcoded password" | False positive: it matched the `ipt_` token **prefix** constant. Skipped in config. |

## Second finding: the schema was public in production

`scripts/smoke_public.py` and a manual probe of a production-configured instance showed `/docs`,
`/redoc` and `/openapi.json` answering `200` to unauthenticated callers. Not a vulnerability in
itself — every endpoint behind them requires a session — but it publishes the full API surface,
parameter names and models to anyone scanning the host.

Fixed: the routes exist only outside production, `IPTV_ENABLE_DOCS` overrides the default, and the
SPA fallback answers `404` for those paths instead of serving `index.html`. Verified inside the
production-shaped container by `scripts/verify_panel.py` (three `404` rows).

## Third finding: XMLTV entity expansion

Provider guides are external XML. The parser allowed a DOCTYPE — real exports carry one — and left
`resolve_entities` at its default, which is *on*. A malicious guide could therefore define nested
entities and rely on libxml2's amplification heuristic to stop it, rather than the product making
expansion structurally impossible.

Two changes:

- `resolve_entities=False` in the iterparse call. The predefined XML entities still resolve, so
  ordinary guides are unaffected: the 19-case corpus and every EPG test passed unchanged.
- An `XMLSyntaxError` raised **during iteration** — which is where libxml2 reports the amplification
  refusal — is now wrapped into `XmltvError`, so a hostile upload returns `422` instead of a `500`
  with a traceback.

Measured on a six-level bomb: 6 characters and a clean refusal with resolution off, versus 3000
characters at four levels (10⁶ at six) with it on.

## Fourth finding: unbounded request bodies

Playlists and guides are uploaded as JSON strings. Nothing limited their size, so an authenticated
client — or a compromised session — could stream hundreds of megabytes into the process and watch it
be buffered.

`IPTV_MAX_REQUEST_BYTES` (64 MB) is now enforced twice: middleware rejects a declared
`Content-Length` above the limit with `413` before the body is read, and both import handlers check
the decoded string, which covers a chunked upload that declares no length at all.

Audited alongside it and already sound: discovery fetches through the guarded client with an 8 MB
cap, the RSS parser already disabled entity resolution and network access, and login already
throttles per client key.

## Fifth finding: quadratic playlist parsing

The M3U attribute pattern retried `[A-Za-z0-9_.-]+\s*=` at every position, so an unterminated quote
made a single line quadratic. Measured before the fix:

| Attribute length | Time |
|---|---|
| 1 000 chars | 27 ms |
| 8 000 chars | 1.25 s |
| 16 000 chars | 5.2 s |
| 200 000 chars | ~13 minutes (extrapolated) |

Parsing runs on the event loop during import, so one hostile line stalls every other request in the
process. A playlist fetched from a provider URL is attacker-influenced even though the import itself
is admin-triggered.

`parse_attributes` is now a single-pass scanner. The same 200 000-character line parses in 0.08 s,
all eleven M3U tests pass unchanged, and a regression test asserts both the time bound and that a
valid entry after the hostile line still survives.

## Sixth finding: a malformed update could stall the Telegram bot

`poll_once` computed the next offset *before* entering its per-update `try`. An update without a
numeric `update_id`, or a non-dictionary element in the batch, raised outside the guard; the outer
loop caught it, slept five seconds, and re-fetched the same batch — forever. The bot would answer
nothing while the log filled with the same error.

The offset now advances defensively (a bad id does not block the others), the handler ignores
non-dictionaries, and a chat id that is not a number is skipped rather than raised on. Two tests
cover it: one batch mixing five malformed shapes, and three junk inputs to `handle_update`.

Honest severity: the real Bot API always sends a numeric `update_id`, so this needed a malformed
payload. It is robustness hardening, not an exploitable hole — but the failure mode was a silent
outage of the alerting channel, which matters more than the likeliness suggests.

## What this does not prove

- **Container OS packages** are not scanned. A production image should also be scanned (Trivy or
  Grype) and rebuilt on a schedule; that work is listed as absent in `STATUS.md`.
- No SBOM is produced yet.
- No dynamic or DAST scanning has been performed against a running instance.
- The audit says nothing about the application's logic — that is what the 287 tests, the SSRF suite
  and the browser/container acceptance runs are for.
