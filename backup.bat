@echo off
rem ================================================================
rem  Dashbord rektora - database backup
rem  ASCII-only by design, see install.bat for the reason.
rem  The whole system state lives in one file (data\dashboard.db),
rem  so copying that file IS backing up the system.
rem  Run: backup.bat
rem ================================================================
cd /d "%~dp0"
setlocal

set "DB=%~dp0data\dashboard.db"
set "BK=%~dp0data\backup"

if not exist "%DB%" (
  echo   Database file not found: %DB%
  echo   The dashboard has probably never been started.
  pause
  exit /b 1
)

if not exist "%BK%" mkdir "%BK%"

set "STAMP=%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "STAMP=%STAMP: =0%"
set "TARGET=%BK%\dashboard_%STAMP%.db"

copy "%DB%" "%TARGET%" >nul
rem Copy the write-ahead log too: if the server is running, the most
rem recent changes may not be in the main file yet.
if exist "%DB%-wal" copy "%DB%-wal" "%TARGET%-wal" >nul
if exist "%DB%-shm" copy "%DB%-shm" "%TARGET%-shm" >nul

echo   Backup created: %TARGET%

rem Keep the 30 newest backups, drop the rest
for /f "skip=30" %%f in ('dir /b /o-d "%BK%\dashboard_*.db" 2^>nul') do del "%BK%\%%f" >nul 2>&1

set /a COUNT=0
for %%f in ("%BK%\dashboard_*.db") do set /a COUNT+=1
echo   Backups kept in folder: %COUNT%
echo   Folder: %BK%
echo.
pause