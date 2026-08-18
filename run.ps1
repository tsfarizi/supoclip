#requires -Version 5.1
<#
  SupoClip native stack launcher - one command to run every application:
  worker (arq), API (FastAPI), frontend (Next.js production server via
  `next start`, built with `next build`), optional MCP server, and optional
  native ASR service. PostgreSQL and Redis must be reachable; this script
  auto-bootstraps both when they are missing.
#>
[CmdletBinding()]
param(
    [switch]$SkipDeps,      # skip dependency install steps (uv sync / pnpm install)
    [switch]$SkipBuild,     # skip frontend production build (start existing .next)
    [switch]$Rebuild,       # force frontend production rebuild
    [switch]$SkipWorker,
    [switch]$SkipFrontend,
    [switch]$IncludeMcp,
    [switch]$IncludeAsr
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$localRoot = Join-Path $repo '.local'
$logDir = Join-Path $localRoot 'logs'
$pidDir = Join-Path $localRoot 'pids'
$dataDir = Join-Path $repo 'backend\data'
New-Item -ItemType Directory -Path $logDir, $pidDir, (Join-Path $dataDir 'uploads'), (Join-Path $dataDir 'clips'), (Join-Path $dataDir 'broll') -Force | Out-Null

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "    $msg" -ForegroundColor Green }
function Write-WarnMsg([string]$msg) { Write-Host "    WARN: $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "    ERROR: $msg" -ForegroundColor Red }

function Test-Port([string]$hostName, [int]$port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($hostName, $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1500)
        if ($ok -and $client.Connected) { $client.Close(); return $true }
        $client.Close(); return $false
    } catch { return $false }
}

# --- 1. proto toolchain -----------------------------------------------------
$protoBin = Join-Path $env:USERPROFILE '.proto\bin'
if (Test-Path (Join-Path $protoBin 'proto.exe')) {
    $env:PATH = "$protoBin;$env:PATH"
} elseif (-not (Get-Command proto -ErrorAction SilentlyContinue)) {
    Write-Fail "proto is not installed. Install it from https://moonrepo.dev/docs/proto/install and re-run."
    exit 1
}
if (-not $SkipDeps) {
    Write-Step "Ensuring pinned toolchain (node/pnpm/python/deno/uv from .prototools)"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & proto install 2>&1 | Out-Null } finally { $ErrorActionPreference = $prevEap }
}

# --- 2. load root .env into process env ------------------------------------
$envFile = Join-Path $repo '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $idx = $t.IndexOf('=')
        if ($idx -le 0) { continue }
        $key = $t.Substring(0, $idx).Trim()
        $val = $t.Substring($idx + 1).Trim().Trim('"')
        if (-not [System.Environment]::GetEnvironmentVariable($key, 'Process')) {
            [System.Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
    Write-Ok "Loaded .env"
} else {
    Write-WarnMsg ".env not found; copy .env.example to .env"
}

# --- 3. infrastructure env --------------------------------------------------
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = 'postgresql+asyncpg://supoclip:supoclip_password@localhost:5433/supoclip' }
if (-not $env:REDIS_HOST) { $env:REDIS_HOST = '127.0.0.1' }
if (-not $env:REDIS_PORT) { $env:REDIS_PORT = '6379' }
if (-not $env:NEXT_PUBLIC_API_URL) { $env:NEXT_PUBLIC_API_URL = 'http://localhost:8000' }
if (-not $env:BACKEND_INTERNAL_URL) { $env:BACKEND_INTERNAL_URL = 'http://localhost:8000' }
$env:TEMP_DIR = Join-Path $dataDir '.'   # API + worker share one absolute tree
$env:PYTHONUNBUFFERED = '1'

# --- 4. ffmpeg --------------------------------------------------------------
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffprobe) {
    $ffBase = Join-Path $env:LOCALAPPDATA 'Programs\ffmpeg'
    $ffmpegBin = Get-ChildItem $ffBase -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName 'bin' } |
        Where-Object { Test-Path (Join-Path $_ 'ffprobe.exe') } |
        Select-Object -First 1
    if ($ffmpegBin) { $env:PATH = "$ffmpegBin;$env:PATH"; Write-Ok "ffmpeg found at $ffmpegBin" }
    else { Write-WarnMsg "ffprobe/ffmpeg not found on PATH - video processing will fail at render time. Install ffmpeg (e.g. portable build under %LOCALAPPDATA%\Programs\ffmpeg)." }
} else { Write-Ok "ffprobe found on PATH" }

# --- 5. Redis ---------------------------------------------------------------
Write-Step "Checking Redis on ${env:REDIS_HOST}:${env:REDIS_PORT}"
$redisUp = Test-Port $env:REDIS_HOST ([int]$env:REDIS_PORT)
if (-not $redisUp) {
    $redisExe = Join-Path (Join-Path $env:LOCALAPPDATA 'Programs\redis') 'redis-server.exe'
    if (Test-Path $redisExe) {
        Write-Ok "Starting portable redis-server"
        Start-Process -FilePath $redisExe -ArgumentList '--bind','0.0.0.0','--port',$env:REDIS_PORT -WindowStyle Hidden
        Start-Sleep -Seconds 2
        $redisUp = Test-Port $env:REDIS_HOST ([int]$env:REDIS_PORT)
    }
}
if (-not $redisUp) { Write-Fail "Redis is not reachable. Install redis-server (portable under %LOCALAPPDATA%\Programs\redis) or start one on ${env:REDIS_HOST}:${env:REDIS_PORT}."; exit 1 }
Write-Ok "Redis reachable"

# --- 6. PostgreSQL schema ---------------------------------------------------
Write-Step "Checking PostgreSQL + schema"
$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) { $psql18 = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'; if (Test-Path $psql18) { $psql = $psql18 } }
if (-not $psql) { Write-Fail "psql not found; PostgreSQL must be installed and running."; exit 1 }
$adminPass = if ($env:POSTGRES_ADMIN_PASSWORD) { $env:POSTGRES_ADMIN_PASSWORD } else { 'postgres' }

function Invoke-Psql([string]$role, [string]$database, [string]$sql) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $psql -U $role -h localhost -p 5433 -d $database -c $sql 2>&1 | Out-Null } catch { }
    $script:pgExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
}

$env:PGPASSWORD = 'supoclip_password'
Invoke-Psql 'supoclip' 'supoclip' "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='users';"
$dbOk = ($script:pgExit -eq 0)
if (-not $dbOk) {
    Write-Ok "Bootstraping supoclip role/database/schema"
    $env:PGPASSWORD = $adminPass
    Invoke-Psql 'postgres' 'postgres' "CREATE ROLE supoclip LOGIN PASSWORD 'supoclip_password';"
    Invoke-Psql 'postgres' 'postgres' "CREATE DATABASE supoclip OWNER supoclip;"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $psql -U postgres -h localhost -p 5433 -d supoclip -v ON_ERROR_STOP=1 -f (Join-Path $repo 'init.sql') 2>&1 | Out-Null } finally { $ErrorActionPreference = $prevEap }
    if ($LASTEXITCODE -ne 0) { Write-Fail "init.sql failed; check PostgreSQL admin credentials (POSTGRES_ADMIN_PASSWORD)."; exit 1 }
    $ownSql = "ALTER TABLE users OWNER TO supoclip; ALTER TABLE sources OWNER TO supoclip; ALTER TABLE tasks OWNER TO supoclip; ALTER TABLE generated_clips OWNER TO supoclip; ALTER TABLE processing_cache OWNER TO supoclip; ALTER TABLE session OWNER TO supoclip; ALTER TABLE account OWNER TO supoclip; ALTER TABLE verification OWNER TO supoclip; ALTER TABLE stripe_webhook_events OWNER TO supoclip; ALTER TABLE revenuecat_webhook_events OWNER TO supoclip; ALTER TABLE app_settings OWNER TO supoclip; ALTER TABLE api_keys OWNER TO supoclip;"
    Invoke-Psql 'postgres' 'supoclip' $ownSql
    Invoke-Psql 'postgres' 'supoclip' "GRANT ALL ON ALL TABLES IN SCHEMA public TO supoclip; GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO supoclip; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO supoclip; ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO supoclip;"
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Write-Ok "Schema bootstrapped"
} else {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Write-Ok "Schema present"
}

# --- 7. versioned migrations -------------------------------------------------
# Alembic is the single migration authority for the backend schema. init.sql
# (step 6) bootstraps fresh databases; the guarded baseline revision then
# converges every database state (fresh / legacy-migrated) to head without
# destructive DDL, so existing data is never touched. The old psql *.sql loop
# and the schema_migrations ledger are retired (no remaining readers).
Write-Step "Running Alembic migrations"
$alembicIni = Join-Path $repo 'backend\src\migrations\alembic.ini'
$backendDir = Join-Path $repo 'backend'
$venvPy = Join-Path $repo 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Step "uv sync (backend) for alembic"
    Push-Location $backendDir
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & uv sync 2>&1 | Select-Object -Last 3 } finally { $ErrorActionPreference = $prevEap; Pop-Location }
    if ($LASTEXITCODE -ne 0) { Write-Fail "uv sync failed; cannot run alembic."; exit 1 }
}
if (-not (Test-Path $alembicIni)) { Write-Fail "alembic.ini missing at $alembicIni"; exit 1 }
# Alembic must target the database that step 6 bootstrapped (localhost:5433),
# mirroring the old psql loop which hardcoded that endpoint; the caller's
# DATABASE_URL is restored afterwards so later steps keep their own target.
$savedDbUrl = $env:DATABASE_URL
$env:DATABASE_URL = 'postgresql+asyncpg://supoclip:supoclip_password@localhost:5433/supoclip'
Push-Location $backendDir
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { & $venvPy -m alembic -c $alembicIni upgrade head 2>&1 | Select-Object -Last 5 } finally { $ErrorActionPreference = $prevEap; Pop-Location }
$env:DATABASE_URL = $savedDbUrl
if ($LASTEXITCODE -ne 0) {
    Write-Fail "alembic upgrade head failed; startup aborted."
    exit 1
}
Write-Ok "Alembic migrations applied"

# --- 8. dependency install --------------------------------------------------
if (-not $SkipDeps) {
    if (-not (Test-Path (Join-Path $repo 'backend\.venv\Scripts\python.exe'))) {
        Write-Step "uv sync (backend)"
        Push-Location (Join-Path $repo 'backend')
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & uv sync 2>&1 | Select-Object -Last 3 } finally { $ErrorActionPreference = $prevEap; Pop-Location }
    } else { Write-Ok "backend/.venv exists" }
    if (-not (Test-Path (Join-Path $repo 'frontend\node_modules'))) {
        Write-Step "pnpm install + prisma generate (frontend)"
        Push-Location (Join-Path $repo 'frontend')
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & pnpm install --frozen-lockfile 2>&1 | Select-Object -Last 3; & pnpm exec prisma generate 2>&1 | Out-Null } finally { $ErrorActionPreference = $prevEap; Pop-Location }
    } else { Write-Ok "frontend/node_modules exists" }
    if ($IncludeMcp -and -not (Test-Path (Join-Path $repo 'mcp\.venv\Scripts\supoclip-mcp.exe'))) {
        Write-Step "uv sync (mcp)"
        Push-Location (Join-Path $repo 'mcp')
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & uv sync 2>&1 | Select-Object -Last 3 } finally { $ErrorActionPreference = $prevEap; Pop-Location }
    }
}

# --- 8. process launcher ----------------------------------------------------
function Start-Bg([string]$name, [string]$file, [string[]]$procArgs, [string]$workdir) {
    $out = Join-Path $logDir "$name.out.log"
    $err = Join-Path $logDir "$name.err.log"
    $p = Start-Process -FilePath $file -ArgumentList $procArgs -WorkingDirectory $workdir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    Set-Content -Path (Join-Path $pidDir "$name.pid") -Value $p.Id
    Write-Ok "$name started (pid $($p.Id)) - logs: $logDir\$name.*.log"
    return $p
}

function Port-Listening([int]$port) {
    return Test-Port '127.0.0.1' $port
}

$venvPy = Join-Path $repo 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { Write-Fail "backend/.venv missing - run without -SkipDeps first."; exit 1 }

# Worker
if (-not $SkipWorker) {
    if (-not $env:ASR_BASE_URL) { $env:ASR_BASE_URL = 'http://localhost:8765' }
    Start-Bg 'worker' $venvPy @('-m','arq','src.workers.tasks.WorkerSettings') (Join-Path $repo 'backend')
}

# API
if (-not (Port-Listening 8000)) {
    Start-Bg 'backend' $venvPy @('-m','uvicorn','src.main_refactored:app','--host','127.0.0.1','--port','8000') (Join-Path $repo 'backend')
} else { Write-Ok "backend already listening on 8000" }

# Frontend (production server)
if (-not $SkipFrontend -and -not (Port-Listening 3107)) {
    # Prisma rejects the asyncpg driver prefix; derive a plain postgresql://
    # URL for the frontend process only (backend/worker keep the asyncpg URL).
    $prismaDbUrl = $env:DATABASE_URL -replace '^postgresql\+asyncpg://', 'postgresql://'
    $savedDbUrl = $env:DATABASE_URL
    $env:DATABASE_URL = $prismaDbUrl

    # Production build: build when missing, or when -Rebuild forces it.
    # Use -SkipBuild to start the existing .next without rebuilding.
    $buildIdPath = Join-Path $repo 'frontend\.next\BUILD_ID'
    if ($Rebuild -or -not (Test-Path $buildIdPath)) {
        if ($SkipBuild) {
            Write-Fail "-SkipBuild given but no production build at frontend\.next - run without -SkipBuild (or with -Rebuild) first."
            exit 1
        }
        Write-Step "Building frontend (next build)"
        Push-Location (Join-Path $repo 'frontend')
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $buildOut = & pnpm build 2>&1
            $buildExit = $LASTEXITCODE
            $buildOut | Select-Object -Last 15
            if ($buildExit -ne 0) { throw "next build failed (exit $buildExit)" }
        } finally {
            $ErrorActionPreference = $prevEap
            Pop-Location
        }
        Write-Ok "frontend build complete"
    } elseif ($SkipBuild) {
        Write-Ok "frontend build skipped (-SkipBuild)"
    } else {
        Write-Ok "frontend production build present (use -Rebuild to force a rebuild)"
    }

    Start-Bg 'frontend' 'cmd.exe' @('/c','pnpm exec next start --port 3107') (Join-Path $repo 'frontend')
    $env:DATABASE_URL = $savedDbUrl
} elseif (-not (Port-Listening 3107)) { Write-Ok "frontend skipped (-SkipFrontend)" }
else { Write-Ok "frontend already listening on 3107" }

# MCP (optional)
if ($IncludeMcp) {
    $mcpExe = Join-Path $repo 'mcp\.venv\Scripts\supoclip-mcp.exe'
    if (Test-Path $mcpExe) {
        if (-not (Port-Listening 9100)) { Start-Bg 'mcp' $mcpExe @() (Join-Path $repo 'mcp') }
        else { Write-Ok "mcp already listening on 9100" }
    } else { Write-WarnMsg "-IncludeMcp given but mcp/.venv missing; run without -SkipDeps" }
}

# ASR (optional, native)
if ($IncludeAsr -or $env:TRANSCRIPT_PROVIDER -eq 'local_asr') {
    $asrPy = Join-Path $repo 'asr\.venv\Scripts\python.exe'
    if (Test-Path $asrPy) {
        if (-not (Port-Listening 8765)) {
            $env:HF_HOME = Join-Path $repo 'asr\models'
            $torchOk = $true
            try {
                & $asrPy -c "import torch" | Out-Null
                if ($LASTEXITCODE -ne 0) { $torchOk = $false }
            } catch { $torchOk = $false }
            if (-not $torchOk -and -not $env:ASR_FAKE_MODEL) {
                Write-WarnMsg "torch missing in asr/.venv - starting ASR with ASR_FAKE_MODEL=1 (deterministic fake transcription, dev-only). Run 'cd asr; uv sync --extra gpu' for real transcription."
                $env:ASR_FAKE_MODEL = '1'
            }
            Start-Bg 'asr' $asrPy @('-m','uvicorn','src.main:app','--host','127.0.0.1','--port','8765') (Join-Path $repo 'asr')
        } else { Write-Ok "asr already listening on 8765" }
    } elseif ($env:TRANSCRIPT_PROVIDER -eq 'local_asr') {
        Write-WarnMsg "TRANSCRIPT_PROVIDER=local_asr but asr/.venv is missing. Run: cd asr; uv sync --extra gpu  (or set TRANSCRIPT_PROVIDER=assemblyai with ASSEMBLY_AI_API_KEY)."
    }
}

# --- 9. health checks -------------------------------------------------------
Write-Step "Waiting for services"
function Wait-Port([int]$port, [int]$seconds, [string]$label) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Port-Listening $port) { Write-Ok "$label up on $port"; return $true }
        Start-Sleep -Seconds 1
    }
    Write-WarnMsg "$label did not come up on $port within ${seconds}s - check $logDir"
    return $false
}
Wait-Port 6379 5 'redis'
Wait-Port 8000 30 'backend API'
Wait-Port 3107 45 'frontend'
$health = $null
try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5 } catch {}
if ($health) { Write-Ok "API /health -> $($health.status)" }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  SupoClip native stack is running" -ForegroundColor Green
Write-Host "  Frontend : http://localhost:3107  (production)" -ForegroundColor Green
Write-Host "  API      : http://localhost:8000  (docs: /docs)" -ForegroundColor Green
Write-Host "  Redis    : localhost:6379" -ForegroundColor Green
Write-Host "  Postgres : localhost:5433 (db: supoclip)" -ForegroundColor Green
Write-Host "  Logs     : $logDir" -ForegroundColor Green
Write-Host "  Stop     : .\stop.ps1" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
