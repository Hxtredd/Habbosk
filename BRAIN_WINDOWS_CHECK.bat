@echo off
cd /d "%~dp0"
rem SK Brain - Windows check (development tool). Changes nothing of yours:
rem tests run in temporary folders; your data files are only READ (checked by hash).
rem The app packages for the full test suite go into .venv-check in THIS folder only.
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
rem SK Brain's own pinned package (pypdf, for PDF import): exact version, hash-checked.
if exist ".venv-check\Scripts\python.exe" (
    ".venv-check\Scripts\python.exe" -m pip install --require-hashes -r requirements-brain.txt
)
if exist ".venv-check\Scripts\python.exe" (
    ".venv-check\Scripts\python.exe" -m puzzle_assistant.devtools.brain_windows_check
) else (
    echo Could not set up .venv-check - running without the app packages.
    %PY% -m puzzle_assistant.devtools.brain_windows_check
)
echo.
pause
