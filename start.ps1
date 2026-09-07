# start.ps1 — one-command launcher for the NextXR platform (Windows / PowerShell).
#
#   ./start.ps1            full stack: Docker (Neo4j+Redis) + backend, AUTH ON,
#                          serving the built UI. Sign in at /login.
#   ./start.ps1 -NoAuth    open the API with no credential (NXR_DEV_MODE=1).
#                          There is then no login page and no persona, so the
#                          role-based pages have nothing to render.
#   ./start.ps1 -NoDocker  backend only, degraded (no Neo4j/Redis) — UI still loads
#   ./start.ps1 -Dev       also start the Vite dev server (npm run dev) for live reload
#   ./start.ps1 -Build     rebuild the frontend before starting
#
# This script makes the "503 / blank UI" problem self-healing: it detects when
# Docker is down, starts it, and waits for Neo4j before launching the server.
#
# NOTE ON AUTHENTICATION: authentication is ON BY DEFAULT here, matching the API's
# own default (server/auth.py) and the posture that actually ships.
#
# It used to be the other way round — open unless you passed -Secure — and that
# was a trap rather than a convenience. The open posture has no session, so it
# has no persona; Dispatch and My Work correctly render "nobody is signed in",
# and there is no login page to fix it from. Someone starting the app the
# obvious way saw a twin with the DB offline, an in-memory bus and no way to
# sign in, and had no reason to suspect a flag. Opting OUT is now the deliberate
# act, which is the direction that fails safe.

param(
    [switch]$NoDocker,
    [switch]$Dev,
    [switch]$Build,
    # Opt OUT of authentication. See the note above on why this is the flag
    # rather than its opposite.
    [switch]$NoAuth,
    # Accepted and ignored: -Secure was how you asked for the posture that is now
    # the default. Kept so existing habits and docs do not error.
    [switch]$Secure
)

# One name for the posture, derived once, so the boot log and the environment
# cannot disagree about which mode this run is in.
$AuthOn = -not $NoAuth

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

# ── .env -> this process ────────────────────────────────────────────
#
# `docker compose` reads .env by itself, so REDIS_PORT reached the CONTAINER.
# The backend runs on the HOST, and nothing put those values in its environment:
# the only reason ANTHROPIC_API_KEY ever worked was that copilot/config.py calls
# load_dotenv() as an import side effect, which is fragile and does not run early
# enough to be relied on. NXR_REDIS_URL had no such accident, so the bus fell
# back to redis://localhost:6379 — another project's Redis on this machine.
#
# Existing environment variables WIN: a value exported in the shell is a
# deliberate override for one run and must not be clobbered by a file.
function Import-DotEnv($path) {
    if (-not (Test-Path $path)) { return }
    $loaded = @()
    foreach ($line in Get-Content $path) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        $eq = $t.IndexOf("=")
        if ($eq -lt 1) { continue }
        $name = $t.Substring(0, $eq).Trim()
        $value = $t.Substring($eq + 1).Trim().Trim('"').Trim("'")
        if ([System.Environment]::GetEnvironmentVariable($name)) { continue }
        [System.Environment]::SetEnvironmentVariable($name, $value)
        $loaded += $name
    }
    if ($loaded.Count) { Write-Ok "loaded from .env: $($loaded -join ', ')" }
}

Write-Step "Configuration"
Import-DotEnv (Join-Path $root ".env")

# ── 1. Docker / databases ───────────────────────────────────────────
if (-not $NoDocker) {
    Write-Step "Checking Docker"
    if (Test-DockerUp) {
        Write-Ok "Docker daemon is running"
    } else {
        Write-Warn "Docker daemon not responding — starting Docker Desktop"
        # SEARCH, do not assume. Docker Desktop installs per-user under
        # %LOCALAPPDATA% as often as it does under Program Files, and this used
        # to hardcode the Program Files path only. On a per-user install the
        # check failed, the script announced "Docker Desktop not found" and fell
        # through to degraded mode — which is how you end up staring at a twin
        # with the database offline and an in-memory bus, with nothing saying
        # the launcher simply looked in one place.
        #
        # The CLI's own location is the last resort and the most reliable: if
        # `docker` is on PATH, Desktop is beside it.
        $candidates = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
            "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
            "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
        )
        $cli = (Get-Command docker -ErrorAction SilentlyContinue).Source
        if ($cli) {
            # ...\resources\bin\docker.exe  ->  ...\Docker Desktop.exe
            $guess = Join-Path (Split-Path (Split-Path (Split-Path $cli))) "Docker Desktop.exe"
            $candidates += $guess
        }
        $dockerExe = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if ($dockerExe) {
            Write-Host "  found: $dockerExe" -ForegroundColor DarkGray
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
            Write-Warn "Docker Desktop not found in any known location."
            Write-Warn "Searched: Program Files, %LOCALAPPDATA%\Programs\DockerDesktop, and next to the docker CLI."
            Write-Warn "Running in DEGRADED mode (no Neo4j, no Redis, in-memory bus)."
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

# ── 3. Free port 8080 (kill stale servers — the recurring gotcha) ───
#
# A plain "kill the LISTEN owner" isn't enough: a server started with multiple
# workers (uvicorn --workers / multiprocessing) leaves the listening socket
# inherited by a *child* worker. When the parent dies, the socket lingers owned
# by a now-dead/phantom PID while the orphaned child still holds it — so killing
# the listed owner does nothing and :8080 stays occupied. We therefore (a) tree-
# kill live owners and (b) hunt down orphaned `--multiprocessing-fork` workers,
# retrying until the LISTEN socket is gone.
Write-Step "Ensuring port 8080 is free"

function Get-Port8080Listeners {
    Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
}

$freed = $false
for ($attempt = 1; $attempt -le 5; $attempt++) {
    $pids = Get-Port8080Listeners
    if (-not $pids) { $freed = $true; break }

    foreach ($procId in $pids) {
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            # Live owner: /T tree-kills its worker children too.
            taskkill /F /T /PID $procId *> $null
            Write-Ok "killed stale server (PID $procId)"
        } else {
            # Phantom owner (dead PID still on the socket). The real holder is an
            # orphaned multiprocessing worker — find and kill those.
            Write-Warn "port 8080 held by a dead PID ($procId) — clearing orphaned workers"
            Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -match 'multiprocessing-fork|multiprocessing\.spawn' } |
                ForEach-Object {
                    taskkill /F /PID $_.ProcessId *> $null
                    Write-Ok "killed orphaned worker (PID $($_.ProcessId))"
                }
        }
    }
    Start-Sleep -Seconds 1
}

if ($freed) { Write-Ok "port 8080 is free" }
else { Write-Warn "port 8080 still occupied after 5 attempts — close the process manually, then re-run" }

# ── 4. Optional Vite dev server ─────────────────────────────────────
if ($Dev) {
    Write-Step "Starting Vite dev server (npm run dev) in a new window"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontend'; npm run dev"
    Write-Ok "Dev server starting at http://localhost:5173"
}

# ── 5. Local-development posture ────────────────────────────────────
#
# THE API NOW DEFAULTS TO CLOSED. `server/auth.py` used to serve any request
# without a credential whenever NXR_API_KEYS was unset, which meant every
# deployment that forgot an environment variable was open to the internet. That
# default is inverted: authentication is REQUIRED unless something explicitly
# opts out.
#
# NXR_DEV_MODE is that opt-out, and it lives HERE rather than in the code so the
# escape hatch is something a developer runs deliberately on a laptop, not a hole
# a deploy inherits by forgetting a variable. Pass -Secure to run the local
# server in the production posture instead — worth doing before a release, since
# it is the configuration that will actually ship.
if ($AuthOn) {
    Write-Step "Authentication REQUIRED (sign in at /login)"
    $env:NXR_DEV_MODE = $null
    $env:NXR_REQUIRE_AUTH = "1"
    if (-not $env:NXR_JWT_SECRET) {
        # The app refuses to boot with an ephemeral signing key while auth is
        # enforced (identity/tokens.py) — each task would sign tokens the others
        # reject. A fixed local value keeps sessions alive across restarts.
        $env:NXR_JWT_SECRET = "local-development-jwt-secret-not-for-production-use"
        Write-Warn "NXR_JWT_SECRET not set — using a fixed local value"
    }
    if (-not $env:NEO4J_PASSWORD) { $env:NEO4J_PASSWORD = "nextxr2026" }
    Write-Ok "auth enforced. Create an account at http://localhost:8080/signup"
    $env:NXR_ALLOW_SIGNUP = "1"
    $env:NXR_COOKIE_SECURE = "0"   # plain HTTP locally; a Secure cookie is never sent
} else {
    $env:NXR_DEV_MODE = "1"
    Write-Warn "NXR_DEV_MODE=1 — the API is OPEN (no credential required)."
    Write-Warn "There is NO login page in this mode, and no persona: Dispatch and"
    Write-Warn "My Work will say 'nobody is signed in'. Drop -NoAuth to sign in."
}

# Migrations. Local dev is a single process, so applying them at boot is safe
# here — on ECS several tasks would race and NXR_AUTO_MIGRATE stays 0 (a one-off
# task runs them before the service rolls).
$env:NXR_AUTO_MIGRATE = "1"

# ── 6. Backend ──────────────────────────────────────────────────────
Write-Step "Starting the NextXR backend"
Write-Host "  → App:  http://localhost:8080" -ForegroundColor Green
Write-Host "  → API:  http://localhost:8080/docs" -ForegroundColor Green
if ($AuthOn) { Write-Host "  → Sign in: http://localhost:8080/login" -ForegroundColor Green }
if ($Dev) { Write-Host "  → Dev:  http://localhost:5173 (live reload)" -ForegroundColor Green }
Write-Host ""
Push-Location $ontology
try { python -m server.main } finally { Pop-Location }
