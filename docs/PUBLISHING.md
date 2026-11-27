# Publishing the repository

The code exists only on this workstation. CI has never run, and Coolify cannot build from a local
directory — it needs a Git remote. This is the missing step, and it needs your GitHub credentials.

A complete history bundle is prepared next to the project:

```
C:\Users\shcgl\Desktop\iptv-control-center-1.6.6.bundle
```

`git bundle verify` reports *"The bundle records a complete history"* and lists `refs/heads/main`
plus tags up to `v1.6.6`.

**The restore path was exercised, not assumed.** Cloning that bundle into a scratch directory gives a
repository at `main` — the commit tagged `v1.7.9` plus the documentation commits after it — with every
tag present, `import iptvcc` reporting `1.7.9`, the copy's own `check_spec.py` and `check_docs.py`
passing, and 26 tests running green from it. So a lost workstation costs a clone, not a rewrite.

## 1. Create an empty repository

On GitHub, create a repository — private is fine — with **no** README, license or `.gitignore`
(those already exist here). Note the URL, e.g. `git@github.com:shcglv/iptv-control-center.git`.

Before pushing, run the preparation script — it refuses a dirty tree, scans tracked files for
obvious secrets, runs ruff and mypy, and regenerates the bundle:

```powershell
.\scripts\publish.ps1
# or, to include the test suite:
.\scripts\publish.ps1 -WithTests
```

It never pushes. When it prints the commands, run them yourself.

## 2. Push from this workstation

```powershell
cd C:\Users\shcgl\Desktop\IPTV-CONTROL-CENTER
git remote add origin git@github.com:shcglv/iptv-control-center.git
git push -u origin main
git push origin v1.0.0
```

If SSH is not set up, use HTTPS and a personal access token instead:

```powershell
git remote add origin https://github.com/shcglv/iptv-control-center.git
git push -u origin main
```

## 3. If the workstation is lost

The bundle restores everything:

```powershell
git clone C:\Users\shcgl\Desktop\iptv-control-center-1.0.0.bundle iptv-control-center
cd iptv-control-center
git remote add origin git@github.com:shcglv/iptv-control-center.git
git push -u origin main --tags
```

## 4. Connect Coolify

Coolify builds through a GitHub App, per the established setup (`shcglv-repo`):

1. Install the GitHub App on the new repository.
2. Coolify → Application → source: the repository, branch `main`, build pack `Dockerfile`.
3. Continue with [`DEPLOYMENT.md`](DEPLOYMENT.md) from step 2.

## 5. First CI run

The workflow at `.github/workflows/ci.yml` runs on push to `main`: backend on SQLite, backend on
PostgreSQL 16, web build, image build, and the container acceptance suite. It has never executed —
the first push is also its first test. If a job fails, the log is the next debugging session.

## What is *not* in the bundle

- `data/` (scratch databases, benchmarks, live-check scripts) — regenerated locally.
- `src/iptvcc/static/` and `web/node_modules/` — built by CI and by the Dockerfile.
- `.env` files — only `.env.example` is tracked.

## What a tag means

A tag marks a **release of the product**: a commit whose behaviour was accepted by the gate. Commits
that only change tests, drills, documentation or the verification tooling may land after the tag
without one, and that is deliberate — chasing `tag == HEAD` for a corrected test would produce an
endless series of patch releases with nothing in them.

The rule this repository follows:

| Change | Tag |
|---|---|
| product behaviour (API, delivery, EPG, security, image) | yes, after the gate |
| tests, drills, documentation, tooling | no; they are verification scaffolding |

The handover bundle always carries `main`, so it includes the scaffolding; the tag inside it names the
release you are running. `RELEASE.md` states which release that is, and `docs/VERIFICATION.md`
records the gate result for it.

## Bumping the version

Use the script, not a text replace:

```powershell
.\.venv\Scripts\python.exe scripts\bump_version.py 1.8.0 --dry-run
.\.venv\Scripts\python.exe scripts\bump_version.py 1.8.0
```

It updates exactly the places that state the *current* version — pyproject, the package, the README
status line, the RELEASE title and version bullet, the START-HERE title, and the STATUS and
VERIFICATION headers — and refuses if any of them disagrees with the current version.

A blanket replace was used until 1.7.7 and it silently relabelled a historical re-acceptance section
as if it had been measured on the newer build. This script cannot do that: it never rewrites another
version number, a measured count, or a section heading.