# Container smoke test — the same checks CI runs after building the image.
#
#   .\scripts\smoke-container.ps1
#   .\scripts\smoke-container.ps1 -Image iptv-control-center:local -Port 8099
#
# Verifies: migrations run at start, /healthz answers, / serves the built SPA, the admin CLI works
# against the migrated database, and the process runs as UID 10001.

param(
    [string]$Image = "iptv-control-center:local",
    [int]$Port = 8099,
    [string]$Container = "iptv-smoke"
)

$ErrorActionPreference = "Stop"
$keyBytes = New-Object byte[] 32
for ($i = 0; $i -lt 32; $i++) { $keyBytes[$i] = [byte](Get-Random -Maximum 256) }
$key = [Convert]::ToBase64String($keyBytes)
if ($key.Length -ne 44) { throw "key generation produced $($key.Length) chars" }

& docker rm -f $Container 2>$null | Out-Null

Write-Host "==> starting $Image" -ForegroundColor Cyan
& docker run -d --name $Container -p "${Port}:8000" `
    -e IPTV_APP_ENV=test `
    -e "IPTV_ENCRYPTION_KEY=$key" `
    -e "IPTV_DATABASE_URL=sqlite+aiosqlite:////tmp/iptv.db" `
    -e "IPTV_PUBLIC_BASE_URL=http://127.0.0.1:$Port" `
    -e IPTV_SCHEDULER_ENABLED=false `
    -e IPTV_TELEGRAM_ENABLED=false `
    $Image | Out-Null

try {
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $code = & curl.exe -s -o NUL -w "%{http_code}" "http://127.0.0.1:$Port/healthz"
        if ($code -eq "200") { $ready = $true; break }
    }
    if (-not $ready) {
        Write-Host "container logs:" -ForegroundColor Yellow
        & docker logs $Container
        throw "the API did not become healthy"
    }
    Write-Host "healthz ok"

    $root = (& curl.exe -s "http://127.0.0.1:$Port/") -join "`n"
    if ($root -notmatch 'id="root"') { throw "the SPA was not served at /" }
    Write-Host "ui ok"

    & docker exec $Container python -m iptvcc.cli create-admin `
        --email smoke@test.local --password correct-horse-battery | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "create-admin failed" }
    Write-Host "admin cli ok"

    $uid = (& docker exec $Container id -u).Trim()
    if ($uid -ne "10001") { throw "process runs as uid $uid, expected 10001" }
    Write-Host "non-root ok (uid $uid)"

    & docker exec $Container python -c "import iptvcc, pathlib; p=pathlib.Path(iptvcc.__file__).parent/'static'/'index.html'; print('static bundled:', p.is_file())"

    Write-Host "Container smoke test passed." -ForegroundColor Green
}
finally {
    & docker rm -f $Container 2>$null | Out-Null
}
