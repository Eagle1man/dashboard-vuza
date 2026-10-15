@echo off
rem ================================================================
rem  Dashbord rektora - installation
rem  This file is ASCII-only ON PURPOSE.
rem  Cyrillic inside a .bat breaks cmd.exe parsing: chcp inside a
rem  batch file makes cmd lose its read position and cut commands
rem  into fragments. All Russian text is printed by backend/setup.py.
rem  Run: install.bat
rem ================================================================
cd /d "%~dp0"
setlocal

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY=python"

if not defined PY (
  echo.
  echo   Python 3.10 or newer was not found.
  echo   Install it from https://www.python.org/downloads/
  echo   and tick "Add Python to PATH" during setup.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   Could not create .venv folder.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m backend.setup
exit /b %ERRORLEVEL%