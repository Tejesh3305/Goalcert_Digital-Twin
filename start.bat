@echo off
REM start.bat — double-click launcher for the NextXR platform (Windows).
REM Delegates to start.ps1, which starts Docker (Neo4j+Redis) if needed,
REM frees port 8000, and runs the backend serving the built UI.
REM
REM Usage from a terminal:
REM   start.bat            full stack
REM   start.bat -NoDocker  backend only (degraded mode, no DB)
REM   start.bat -Dev       also start the Vite dev server
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
