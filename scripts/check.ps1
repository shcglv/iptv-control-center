# Local gates — the same commands CI runs, with a Windows-friendly entry point.
#
#   .\scripts\check.ps1              # everything
#   .\scripts\check.ps1 -SkipWeb     # backend only
#   .\scripts\check.ps1 -Browser     # also run the Playwright tests

param(
    [switch]$SkipWeb,
    [switch]$Browser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "virtualenv not found at $python"
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$Name failed (exit $LASTEXITCODE)"
    }
}

Invoke-Step "ruff check" { & $python -m ruff check src tests }
Invoke-Step "ruff format --check" { & $python -m ruff format --check src tests }
Invoke-Step "mypy" { & $python -m mypy src }
Invoke-Step "pytest" { & $python -m pytest -q }

if ($Browser) {
    $env:IPTV_BROWSER_TESTS = "1"
    Invoke-Step "pytest (browser)" { & $python -m pytest tests/test_browser.py -q }
    Remove-Item Env:\IPTV_BROWSER_TESTS -ErrorAction SilentlyContinue
}

if (-not $SkipWeb) {
    $web = Join-Path $root "web"
    Push-Location $web
    try {
        Invoke-Step "web typecheck" { & pnpm typecheck }
        Invoke-Step "web build" { & pnpm build }
    }
    finally {
        Pop-Location
    }
}

Write-Host "All gates passed." -ForegroundColor Green
