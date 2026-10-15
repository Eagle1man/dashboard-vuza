@echo off
rem ================================================================
rem  Dashbord rektora - launcher
rem  ASCII-only by design, see install.bat for the reason.
rem  Starts the local language model (if installed), the backend
rem  server, and opens the dashboard in a browser.
rem  Run: start.bat
rem ================================================================
cd /d "%~dp0"
setlocal
title Dashbord rektora

set "API=http://127.0.0.1:8765"
set "OLLAMA=http://localhost:11434"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo.
  echo   Not installed yet. Starting installation...
  echo.
  call "%~dp0install.bat"
  if not exist "%PY%" (
    echo   Installation failed.
    pause
    exit /b 1
  )
)

echo.
echo ================================================================
echo   Dashbord rektora
echo ================================================================
echo.
echo [1/3] Checking the local language model...

set "OLLAMA_EXE="
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA_EXE (
  for %%f in (ollama.exe) do (
    if not "%%~$PATH:f"=="" set "OLLAMA_EXE=%%~$PATH:f"
  )
)

curl.exe -s --max-time 3 %OLLAMA%/api/tags >nul 2>&1
if errorlevel 1 if defined OLLAMA_EXE (
  echo       starting Ollama...
  start "" /B "%OLLAMA_EXE%" serve
  timeout /t 4 /nobreak >nul
)

curl.exe -s --max-time 3 %OLLAMA%/api/tags >nul 2>&1
if errorlevel 1 (
  echo       Ollama is not running. The dashboard will still open,
  echo       but the assistant will only list found documents instead
  echo       of summarising them. Install Ollama from https://ollama.com
) else (
  "%PY%" -m backend.modelcheck
)

echo.
echo [2/3] Starting the server...
start "Dashbord rektora - server" /B "%PY%" -m backend.app
echo       waiting for it to come up...

set "READY="
for /l %%i in (1,1,40) do (
  curl.exe -s --max-time 2 %API%/api/health >nul 2>&1
  if not errorlevel 1 (
    set "READY=1"
    goto :open
  )
  timeout /t 1 /nobreak >nul
)

:open
echo.
if defined READY (
  echo [3/3] Opening the dashboard...
  start "" "%API%/"
  echo.
  echo   Ready. Keep this window open - the server runs inside it.
  echo   To shut the dashboard down, just close this window.
  echo.
) else (
  echo [3/3] FAILED: the server did not answer within 40 seconds.
  echo.
  echo   To see the reason, run this command in a terminal:
  echo      .venv\Scripts\python.exe -m backend.app
  echo.
  pause
)
endlocal