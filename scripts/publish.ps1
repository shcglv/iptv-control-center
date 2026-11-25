# Prepare a safe publish: checks, secret scan, fresh bundle, then the exact commands.
#
#   .\scripts\publish.ps1
#   .\scripts\publish.ps1 -RemoteUrl git@github.com:shcglv/iptv-control-center.git
#   .\scripts\publish.ps1 -WithTests
#
# It never pushes. Publishing is a deliberate, credential-bearing action; this only makes sure the
# repository is worth pushing and prints what to run.

param(
    [string]$RemoteUrl = "",
    [switch]$WithTests,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail([string]$Message) {
    Write-Host "FAIL: $Message" -ForegroundColor Red
    exit 1
}

Write-Host "==> repository" -ForegroundColor Cyan
$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") { Fail "expected branch main, on '$branch'" }

$dirty = & git status --porcelain
if ($dirty -and -not $AllowDirty) { Fail "working tree is not clean; commit or pass -AllowDirty" }
$treeState = "clean"
if ($dirty) { $treeState = "dirty (allowed)" }
Write-Host "branch: $branch, tree: $treeState"

$version = (& ".\.venv\Scripts\python.exe" -c "import iptvcc; print(iptvcc.__version__)").Trim()
$head = (& git rev-parse --short HEAD).Trim()
Write-Host "version: $version, HEAD: $head"

Write-Host "==> secret scan" -ForegroundColor Cyan
$patterns = @(
    "BEGIN [A-Z ]*PRIVATE KEY",
    "AKIA[0-9A-Z]{16}",
    "ghp_[A-Za-z0-9]{20,}",
    "xox[baprs]-[A-Za-z0-9-]{10,}",
    "IPTV_ENCRYPTION_KEY=[A-Za-z0-9+/=]{20,}"
)
$findings = 0
foreach ($pattern in $patterns) {
    $hits = & git grep -n -I -E $pattern 2>$null
    if ($LASTEXITCODE -eq 0 -and $hits) {
        Write-Host "  suspicious ($pattern):" -ForegroundColor Yellow
        $hits | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
        $findings++
    }
}
if ($findings -gt 0) { Fail "possible secret in tracked files; inspect before publishing" }
Write-Host "no obvious secrets in tracked files"

Write-Host "==> static checks" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m ruff check src tests scripts
if ($LASTEXITCODE -ne 0) { Fail "ruff failed" }
& ".\.venv\Scripts\python.exe" -m mypy src
if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }

if ($WithTests) {
    Write-Host "==> tests" -ForegroundColor Cyan
    & ".\.venv\Scripts\python.exe" -m pytest -q
    if ($LASTEXITCODE -ne 0) { Fail "tests failed" }
}

Write-Host "==> bundling" -ForegroundColor Cyan
$bundle = Join-Path (Split-Path $root -Parent) "iptv-control-center-$version.bundle"
& git bundle create $bundle --all 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "git bundle failed" }
& git bundle verify $bundle 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "bundle did not verify" }
$size = [math]::Round((Get-Item $bundle).Length / 1KB, 0)
Write-Host "bundle: $bundle ($size KB, verified)"

Write-Host ""
Write-Host "Ready to publish. Nothing has been pushed. Run:" -ForegroundColor Green
Write-Host ""
Write-Host "  cd '$root'"
if ($RemoteUrl) {
    Write-Host "  git remote add origin $RemoteUrl"
} else {
    Write-Host "  git remote add origin YOUR-REPOSITORY-URL"
}
Write-Host "  git push -u origin main"
Write-Host "  git push origin --tags"
Write-Host ""
Write-Host "Then install the Coolify GitHub App on that repository and follow docs/DEPLOYMENT.md."
