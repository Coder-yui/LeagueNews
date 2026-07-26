[CmdletBinding()]
param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $ProjectRoot ".run"
$LogDir = Join-Path $RunDir "logs"
$ApiDir = Join-Path $ProjectRoot "services\api"
$EnvFile = Join-Path $ProjectRoot ".env"
$DockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Require-Command([string]$Name, [string]$InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found. $InstallHint"
    }
}

function Test-DockerDaemon {
    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Wait-Http([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Test-RecordedProcess([string]$Name) {
    $pidFile = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path $pidFile)) {
        return $false
    }
    $savedPid = (Get-Content -Raw $pidFile).Trim()
    if ($savedPid -and (Get-Process -Id $savedPid -ErrorAction SilentlyContinue)) {
        return $true
    }
    Remove-Item -LiteralPath $pidFile -Force
    return $false
}

function Assert-PortAvailable([int]$Port, [string]$Service) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw "$Service cannot start because port $Port is in use. Run scripts\stop.ps1 or close the process using that port."
    }
}

function Start-TrackedProcess(
    [string]$Name,
    [string]$WorkingDirectory,
    [string]$Command
) {
    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.error.log"
    $process = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    Set-Content -LiteralPath (Join-Path $RunDir "$Name.pid") -Value $process.Id
    return $process
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

Require-Command "docker" "Install Docker Desktop first."
Require-Command "uv" "Install uv and reopen PowerShell."
Require-Command "pnpm" "Install pnpm and reopen PowerShell."

if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Run: Copy-Item .env.example .env, then configure OPENAI_API_KEY."
}
if (-not (Test-Path (Join-Path $ApiDir ".venv"))) {
    throw "Backend dependencies are missing. Run 'uv sync --dev' in services\api."
}
if (-not (Test-Path (Join-Path $ProjectRoot "node_modules"))) {
    throw "Frontend dependencies are missing. Run 'pnpm install' in the project root."
}

Write-Step "Checking Docker daemon"
if (-not (Test-DockerDaemon)) {
    if (-not (Test-Path $DockerDesktop)) {
        throw "Docker daemon is unavailable and Docker Desktop was not found at its default path. Start it manually and retry."
    }
    Write-Host "Docker Desktop is not running. Starting it and waiting for the daemon..."
    Start-Process -FilePath $DockerDesktop | Out-Null
    $dockerDeadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $dockerDeadline -and -not (Test-DockerDaemon)) {
        Start-Sleep -Seconds 2
    }
    if (-not (Test-DockerDaemon)) {
        throw "Timed out waiting for Docker Desktop. Wait until it shows Engine running, then retry."
    }
}

Write-Step "Starting PostgreSQL and pgAdmin"
& docker compose up -d
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up -d failed."
}

$apiAlreadyRunning = Test-RecordedProcess "api"
$webAlreadyRunning = Test-RecordedProcess "web"

if (-not $apiAlreadyRunning) {
    Assert-PortAvailable 8000 "FastAPI"
    Write-Step "Starting FastAPI"
    $apiCommand = "`$env:UV_CACHE_DIR='$ProjectRoot\.uv-cache'; uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
    Start-TrackedProcess "api" $ApiDir $apiCommand | Out-Null
} else {
    Write-Host "FastAPI is already tracked as running; skipping."
}

if (-not $webAlreadyRunning) {
    Assert-PortAvailable 3000 "Next.js"
    Write-Step "Starting Next.js"
    Start-TrackedProcess "web" $ProjectRoot "pnpm dev:web" | Out-Null
} else {
    Write-Host "Next.js is already tracked as running; skipping."
}

Write-Step "Waiting for health checks"
$apiReady = Wait-Http "http://localhost:8000/api/v1/health" 45
$webReady = Wait-Http "http://localhost:3000" 60

if (-not $apiReady -or -not $webReady) {
    Write-Host "Not all services became ready before the timeout." -ForegroundColor Yellow
    Write-Host "API log: $LogDir\api.error.log"
    Write-Host "Web log: $LogDir\web.error.log"
    exit 1
}

Write-Step "Applying database migrations"
& docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /migrations/007_add_connector_ingestion.sql'
if ($LASTEXITCODE -ne 0) {
    throw "Migration 007 failed. Check PostgreSQL output above."
}
$postgresUser = (& docker compose exec -T postgres printenv POSTGRES_USER | Out-String).Trim()
$postgresDatabase = (& docker compose exec -T postgres printenv POSTGRES_DB | Out-String).Trim()
if (-not $postgresUser -or -not $postgresDatabase) {
    throw "Could not read PostgreSQL connection settings from the container."
}
$appliedVersions = @(
    & docker compose exec -T postgres psql `
        -U $postgresUser `
        -d $postgresDatabase `
        -tA `
        -c "SELECT version FROM schema_migrations"
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect applied database migrations."
}
foreach (
    $migrationVersion in @(
        "008_seed_web_connector_sources",
        "009_source_identity_per_publisher",
        "010_add_weibo_tieba_sources",
        "011_add_reviewed_ai_workflows",
        "012_track_approved_media_extractions",
        "013_add_ocr_lab",
        "014_add_patch_table_structure",
        "015_raw_items_content_blocks_v2",
        "016_normalize_legacy_embed_urls",
        "017_simplify_raw_item_identity",
        "018_normalize_x_author_names",
        "019_version_raw_item_ingestion",
        "020_reset_processing_data",
        "021_remove_legacy_raw_item_identity_index"
    )
) {
    if ($appliedVersions -notcontains $migrationVersion) {
        & docker compose exec -T postgres sh -c "psql -v ON_ERROR_STOP=1 -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -f /migrations/$migrationVersion.sql"
        if ($LASTEXITCODE -ne 0) {
            throw "Migration $migrationVersion failed. Check PostgreSQL output above."
        }
    }
}

Write-Step "Upgrading RawItem semantic hashes"
Push-Location $ApiDir
try {
    & ".venv\Scripts\python.exe" -m scripts.rebuild_raw_item_hashes
    if ($LASTEXITCODE -ne 0) {
        throw "RawItem hash upgrade failed."
    }
} finally {
    Pop-Location
}

Write-Host "`nLoL Daily Intel is running:" -ForegroundColor Green
Write-Host "  Website    http://localhost:3000"
Write-Host "  API docs   http://localhost:8000/docs"
Write-Host "  pgAdmin    http://localhost:5050"
Write-Host "  Logs       $LogDir"
Write-Host "`nStop with: .\scripts\stop.ps1"

if (-not $SkipBrowser) {
    Start-Process "http://localhost:3000"
    Start-Process "http://localhost:8000/docs"
    Start-Process "http://localhost:5050"
}
