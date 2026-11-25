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

## What this does not prove

- **Container OS packages** are not scanned. A production image should also be scanned (Trivy or
  Grype) and rebuilt on a schedule; that work is listed as absent in `STATUS.md`.
- No SBOM is produced yet.
- No dynamic or DAST scanning has been performed against a running instance.
- The audit says nothing about the application's logic — that is what the 232 tests, the SSRF suite
  and the browser/container acceptance runs are for.
