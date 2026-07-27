[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not $OutputPath) {
    $OutputPath = Join-Path $ProjectRoot "backups\league-news-current-$Timestamp.dump"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $ProjectRoot $OutputPath
}

$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$OutputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$ContainerId = (& docker compose ps -q postgres | Out-String).Trim()
if (-not $ContainerId) {
    throw "The local PostgreSQL container is not running."
}

$ReadyDeadline = (Get-Date).AddSeconds(60)
do {
    & docker compose exec -T postgres sh -c `
        'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' *> $null
    if ($LASTEXITCODE -eq 0) {
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $ReadyDeadline)

if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL did not become ready within 60 seconds."
}

$ContainerBackup = "/tmp/league-news-production-export.dump"
& docker compose exec -T postgres sh -c `
    'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/league-news-production-export.dump'
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed."
}

try {
    & docker cp "${ContainerId}:${ContainerBackup}" $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "docker cp failed."
    }
} finally {
    & docker compose exec -T postgres rm -f $ContainerBackup | Out-Null
}

Write-Host "Database export written to $OutputPath" -ForegroundColor Green
