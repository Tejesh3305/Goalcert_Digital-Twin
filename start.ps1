# start.ps1 — one-command launcher for the NextXR platform (Windows / PowerShell).
#
#   ./start.ps1            full stack: Docker (Neo4j+Redis) + backend, serving the built UI
#   ./start.ps1 -NoDocker  backend only, in degraded mode (no DB) — UI still loads
#   ./start.ps1 -Dev       also start the Vite dev server (npm run dev) for live reload
#   ./start.ps1 -Build     rebuild the frontend before starting
#
# This script makes the "503 / blank UI" problem self-healing: it detects when
# Docker is down, starts it, and waits for Neo4j before launching the server.

param(
    [switch]$NoDocker,
    [switch]$Dev,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$ontology = Join-Path $root "nextxr-ontology"
$frontend = Join-Path $root "frontend"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }

function Test-DockerUp {
    try { docker ps *> $null; return $LASTEXITCODE -eq 0 } catch { return $false }
}

# ── 1. Docker / databases ───────────────────────────────────────────
if (-not $NoDocker) {
    Write-Step "Checking Docker"
    if (Test-DockerUp) {
        Write-Ok "Docker daemon is running"
    } else {
        Write-Warn "Docker daemon not responding — starting Docker Desktop"
        $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if (Test-Path $dockerExe) {
            Start-Process $dockerExe
            Write-Host "  Waiting for Docker to become ready (up to 120s)..." -NoNewline
            $ready = $false
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 2
                Write-Host "." -NoNewline
                if (Test-DockerUp) { $ready = $true; break }
            }
            Write-Host ""
            if ($ready) { Write-Ok "Docker is ready" }
            else {
                Write-Warn "Docker did not come up in time."
                Write-Warn "Falling back to DEGRADED mode (UI works, no live graph data)."
                Write-Warn "Tip: open Docker Desktop manually, wait for the whale icon to settle, then re-run."
                $NoDocker = $true
            }
        } else {
            Write-Warn "Docker Desktop not found — running in DEGRADED mode (no DB)."
            $NoDocker = $true
        }
    }

    if (-not $NoDocker) {
        Write-Step "Starting Neo4j + Redis (docker compose)"
        Push-Location $root
        try {
            docker compose up -d neo4j redis
            Write-Host "  Waiting for Neo4j on :7687 (up to 60s)..." -NoNewline
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Seconds 2
                Write-Host "." -NoNewline
                $ok = (Test-NetConnection -ComputerName localhost -Port 7687 -WarningAction SilentlyContinue).TcpTestSucceeded
                if ($ok) { break }
            }
            Write-Host ""
            if ((Test-NetConnection -ComputerName localhost -Port 7687 -WarningAction SilentlyContinue).TcpTestSucceeded) {
                Write-Ok "Neo4j is reachable"
            } else {
                Write-Warn "Neo4j not reachable yet — the server will run in degraded mode until it is."
            }
        } finally { Pop-Location }
    }
} else {
    Write-Step "Skipping Docker (degraded mode) — UI loads, live graph data is paused"
}

# ── 2. Frontend build (optional) ────────────────────────────────────
if ($Build -or -not (Test-Path (Join-Path $frontend "dist\index.html"))) {
    Write-Step "Building the frontend"
    Push-Location $frontend
    try {
        if (-not (Test-Path "node_modules")) { npm install }
        npm run build
        Write-Ok "Frontend built to frontend/dist"
    } finally { Pop-Location }
}

# ── 3. Free port 8000 (kill stale servers — the recurring gotcha) ───
Write-Step "Ensuring port 8000 is free"
$pids = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
if ($pids) {
    foreach ($procId in $pids) {
        try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue; Write-Ok "killed stale server (PID $procId)" } catch {}
    }
    Start-Sleep -Seconds 1
} else { Write-Ok "port 8000 already free" }

# ── 4. Optional Vite dev server ─────────────────────────────────────
if ($Dev) {
    Write-Step "Starting Vite dev server (npm run dev) in a new window"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontend'; npm run dev"
    Write-Ok "Dev server starting at http://localhost:5173"
}

# ── 5. Backend ──────────────────────────────────────────────────────
Write-Step "Starting the NextXR backend"
Write-Host "  → App:  http://localhost:8000" -ForegroundColor Green
Write-Host "  → API:  http://localhost:8000/docs" -ForegroundColor Green
if ($Dev) { Write-Host "  → Dev:  http://localhost:5173 (live reload)" -ForegroundColor Green }
Write-Host ""
Push-Location $ontology
try { python -m server.main } finally { Pop-Location }
