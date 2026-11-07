@echo off
cd /d "%~dp0"
rem SK Brain - Windows check (development tool). Changes nothing of yours:
rem tests run in a temporary folder; your data files are only READ (checked by hash).
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python was not found. Install it from python.org or run INSTALL.bat first.
    pause
    exit /b 1
)
%PY% -m puzzle_assistant.devtools.brain_windows_check
echo.
pause
