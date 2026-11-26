# Local gates - the same commands CI runs, with a Windows-friendly entry point.
#
#   .\scripts\check.ps1              # everything
#   .\scripts\check.ps1 -SkipWeb     # backend only
#   .\scripts\check.ps1 -Browser     # also run the Playwright tests
#   .\scripts\check.ps1 -Fast        # skip pytest (lint and types only)
#
# Prints one verdict line at the end and exits non-zero on any failure, so a gate that fails cannot
# be mistaken for a notice. The ruff step covers `scripts` as well: a lint error in a tool went
# unnoticed for ten rounds because this file only checked `src tests`.

param(
    [switch]$SkipWeb,
    [switch]$Browser,
    [switch]$Fast
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "GATES FAILED: virtualenv not found at $python" -ForegroundColor Red
    exit 1
}

$script:results = @()

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    if ($code -eq 0) {
        $script:results += [pscustomobject]@{ Step = $Name; Result = "pass" }
    }
    else {
        $script:results += [pscustomobject]@{ Step = $Name; Result = "FAIL ($code)" }
        Write-Host "    FAILED: $Name (exit $code)" -ForegroundColor Red
    }
}

Invoke-Step "ruff check" { & $python -m ruff check src tests scripts }
Invoke-Step "ruff format --check" { & $python -m ruff format --check src tests scripts }
Invoke-Step "mypy" { & $python -m mypy src }

if (-not $Fast) {
    Invoke-Step "pytest" { & $python -m pytest -q }

    if ($Browser) {
        $env:IPTV_BROWSER_TESTS = "1"
        Invoke-Step "pytest (browser)" { & $python -m pytest tests/test_browser.py -q }
        Remove-Item Env:\IPTV_BROWSER_TESTS -ErrorAction SilentlyContinue
    }
}

if (-not $SkipWeb) {
    $web = Join-Path $root "web"
    Push-Location $web
    try {
        Invoke-Step "web typecheck" { pnpm typecheck }
        Invoke-Step "web build" { pnpm build }
    }
    finally {
        Pop-Location
    }
}

Invoke-Step "documentation consistency" { & $python scripts\check_docs.py }
Invoke-Step "specification invariants" { & $python scripts\check_spec.py }

Write-Host ""
$script:results | Format-Table -AutoSize | Out-String | Write-Host
$failed = @($script:results | Where-Object { $_.Result -ne "pass" })
if ($failed.Count -gt 0) {
    Write-Host "GATES FAILED: $($failed.Count) of $($script:results.Count) step(s)" -ForegroundColor Red
    exit 1
}
Write-Host "GATES PASSED ($($script:results.Count) steps)" -ForegroundColor Green
exit 0
