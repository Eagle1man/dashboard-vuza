@echo off
rem ================================================================
rem  Launcher for the Windows scheduled task (see service-install.ps1).
rem  ASCII-only by design: Cyrillic in .bat breaks cmd.exe parsing.
rem  All Russian text is printed by the Python backend.
rem ================================================================
cd /d "%~dp0..\.."

if not exist "logs" mkdir "logs"

rem Publish to the local network: listen on all interfaces, data behind login.
if not defined RECTOR_API_HOST set "RECTOR_API_HOST=0.0.0.0"
if not defined RECTOR_REQUIRE_LOGIN set "RECTOR_REQUIRE_LOGIN=1"
rem 7B on CPU answers for minutes; 300 s is not enough.
if not defined RECTOR_OLLAMA_TIMEOUT set "RECTOR_OLLAMA_TIMEOUT=600"
set "PYTHONUNBUFFERED=1"

".venv\Scripts\python.exe" -m backend.app >> "logs\server.log" 2>&1
