# run.ps1 — start the 3D platform (gateway + pipeline) at http://localhost:8100
#   Right-click → Run with PowerShell, or:  ./run.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# Free the port if a previous run is still bound.
Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { try { Stop-Process -Id $_ -Force } catch {} }

Write-Host "3D Platform -> http://localhost:8100" -ForegroundColor Green
python -m uvicorn app.main:app --reload --port 8100
