[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $ProjectRoot ".run"

function Stop-TrackedProcess([string]$Name) {
    $pidFile = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "${Name}: no PID file; skipping."
        return
    }

    $savedPid = (Get-Content -Raw $pidFile).Trim()
    if ($savedPid -and (Get-Process -Id $savedPid -ErrorAction SilentlyContinue)) {
        Write-Host "Stopping $Name (PID $savedPid)..."
        & taskkill.exe /PID $savedPid /T /F *> $null
    } else {
        Write-Host "${Name}: process has already exited."
    }
    Remove-Item -LiteralPath $pidFile -Force
}

Set-Location $ProjectRoot
Stop-TrackedProcess "web"
Stop-TrackedProcess "api"

Write-Host "Stopping PostgreSQL and pgAdmin containers..."
& docker compose stop
if ($LASTEXITCODE -ne 0) {
    throw "docker compose stop failed. Make sure Docker Desktop is running."
}

Write-Host "`nLoL Daily Intel has stopped." -ForegroundColor Green
Write-Host "Database data remains in the Docker volumes."
Write-Host "Docker Desktop remains open so other projects are not affected."
