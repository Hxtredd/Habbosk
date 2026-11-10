@echo off
setlocal
cd /d "%~dp0"
title SK Assistant TRIAL - setup
echo ============================================================
echo  SK Assistant TRIAL - one-time setup (test version)
echo  This folder only. Your normal SK Assistant, its icon, its
echo  settings and its data are NOT changed.
echo ============================================================
echo.
if exist "%APPDATA%\SK Assistant" (
    echo Your normal SK data folder stays untouched: "%APPDATA%\SK Assistant"
    echo.
)

set "PY="
call :try_py py -3
if not defined PY call :try_py python
if not defined PY call :try_py python3
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :try_py "%%D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PY call :try_py "%%D\python.exe"
if not defined PY (
    echo [ERROR] No working Python 3.9 or newer was found.
    pause
    exit /b 1
)
echo Using Python:
%PY% -c "import sys; print('  ', sys.version.split()[0], sys.executable)"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating the trial's own Python environment ^(.venv in this folder^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the environment.
        pause
        exit /b 1
    )
)
echo [2/3] Installing the same packages SK Assistant uses ^(requirements.txt^)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Package installation failed. Check your internet connection.
    pause
    exit /b 1
)
echo [3/3] Self-test with the isolation guard on ^(about a minute^)...
".venv\Scripts\python.exe" trial\selftest.py
if errorlevel 1 (
    echo.
    echo [WARNING] The self-test did not pass. Please send a screenshot of this window to Claude
    echo           before using the trial.
    pause
    exit /b 1
)
echo.
echo ============================================================
echo  Setup finished. Close your normal SK Assistant, then
echo  double-click TRIAL_START.bat in this folder.
echo  (No Desktop icon is created - your normal icon stays as it is.)
echo ============================================================
pause
exit /b 0

:try_py
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0
