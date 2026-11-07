@echo off
rem ================================================================
rem  Sync IUBiP monitoring card with monitoring.miccedu.ru
rem  Checks the card, updates data only when the site changed,
rem  rebuilds monitoring.json / analytics.db / knowledge index.
rem
rem  Run once:        sync_monitoring.bat
rem  Check only:      sync_monitoring.bat --check
rem  Full region:     sync_monitoring.bat --full
rem  Watch daily:     sync_monitoring.bat --watch --interval 86400
rem ================================================================
cd /d "%~dp0"
setlocal

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo   Not installed yet. Run install.bat first.
  echo.
  exit /b 1
)

"%PY%" -m backend.sync_monitoring %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
