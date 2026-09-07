# start-dev.ps1 — launches the 2-D → 3-D backend + frontend for local review.
#
#   Right-click → "Run with PowerShell", or from a terminal:  ./start-dev.ps1
#
# Why "python -m uvicorn" and not "uvicorn":
#   The bare `uvicorn` command only works if Python's Scripts\ folder is on PATH.
#   `python -m uvicorn` always works because it uses whatever Python is active —
#   this is what fixes the recurring "uvicorn not found" error.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Free-Port($port) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop; "  freed port $port (pid $_)" } catch {} }
}

Write-Host "Freeing ports 8000 / 5174 if busy..." -ForegroundColor Cyan
Free-Port 8000
Free-Port 5174

Write-Host "Starting backend  -> http://localhost:8000" -ForegroundColor Green
Start-Process powershell -ArgumentList @(
  '-NoExit','-Command',
  "Set-Location '$root\backend'; python -m uvicorn app:app --reload --port 8000"
)

Write-Host "Starting frontend -> http://localhost:5174" -ForegroundColor Green
Start-Process powershell -ArgumentList @(
  '-NoExit','-Command',
  "Set-Location '$root\frontend'; npm run dev"
)

Write-Host ""
Write-Host "Open the app at:  http://localhost:5174" -ForegroundColor Yellow
Write-Host "(give it ~15s for the backend reloader + Vite to warm up)" -ForegroundColor DarkGray
