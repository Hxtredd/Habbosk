@echo off
cd /d "%~dp0"
rem SK Assistant - version test record (development tool). Runs the whole test
rem suite on this PC and writes version_test_record.md / .json in this folder.
rem Changes nothing of yours: the tests use a temporary data folder.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python was not found. Install it from python.org first.
    pause
    exit /b 1
)
if not exist ".venv-check\Scripts\python.exe" (
    echo Setting up a private test environment in this folder ^(.venv-check^)...
    %PY% -m venv .venv-check
    ".venv-check\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv-check\Scripts\python.exe" -m pip install -r requirements.txt
)
if exist ".venv-check\Scripts\python.exe" (
    ".venv-check\Scripts\python.exe" -m pip install --require-hashes -r requirements-brain.txt
    ".venv-check\Scripts\python.exe" tools\version_record.py %*
) else (
    %PY% tools\version_record.py %*
)
echo.
pause
