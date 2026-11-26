# Start here

**IPTV Control Center 1.7.7** — a self-hosted control plane for IPTV. It imports provider playlists,
keeps one channel catalog, serves a per-device M3U and XMLTV guide, chooses the best source per
channel, and answers playback with a `302` straight to the provider. **It never carries video.**

## State

Verified release candidate. **Not deployed anywhere.**

- 271 backend tests pass on SQLite and on PostgreSQL 16; 8 browser tests; 3 container acceptance
  tests; ten operational drills, the last full run recorded at commit `72f980c` on 2026-11-26.
- Six security findings were found and fixed; the report is [`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md).
- What is verified, and what is not, is stated in [`docs/VERIFICATION.md`](docs/VERIFICATION.md).
- Every requirement from the approved design, with where it lives and how it is proven:
  [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md).
- What is missing on purpose is listed in [`docs/STATUS.md`](docs/STATUS.md).

## What is blocked, and on whom

Nothing below can be done from this workstation. All three need the owner.

| # | What | Where |
|---|---|---|
| 1 | **GitHub credentials** — the repository has no remote, so CI has never run | [`docs/PUBLISHING.md`](docs/PUBLISHING.md): a verified bundle sits on the Desktop; three commands push it |
| 2 | **A "GO" for the deployment** — nothing on the server has been touched | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): twelve steps, risk register, rollback |
| 3 | **A provider and a TV** — the acceptance run | [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md): 30–40 minutes, checkbox by checkbox |

## Install, in one line each

Ten steps with an environment table: [`RELEASE.md`](RELEASE.md). Then check the result from any
machine, without credentials:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_public.py --base https://iptv.joxit.ru
```

## Reproduce the verification

```powershell
.\.venv\Scripts\python.exe scripts\verify_all.py
```

It detects Docker and network access, skips what is unavailable with a reason, and prints one
summary. `--only panel --only upgrade` runs a subset; `--base URL` adds the smoke drill.

## If something breaks

[`docs/RUNBOOK.md`](docs/RUNBOOK.md) has the symptoms-to-actions table. Backups are drilled in both
directions: [`scripts/verify_pg_container.py`](scripts/verify_pg_container.py) writes one,
[`scripts/verify_restore.py`](scripts/verify_restore.py) restores it into a fresh database and checks
every table.
