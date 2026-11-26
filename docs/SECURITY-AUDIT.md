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

## What this does not prove

- **Container OS packages** are not scanned. A production image should also be scanned (Trivy or
  Grype) and rebuilt on a schedule; that work is listed as absent in `STATUS.md`.
- No SBOM is produced yet.
- No dynamic or DAST scanning has been performed against a running instance.
- The audit says nothing about the application's logic — that is what the 232 tests, the SSRF suite
  and the browser/container acceptance runs are for.
