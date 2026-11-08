@echo off
cd /d "%~dp0"
rem SK Brain - read-only audit of your knowledge.db (development tool).
rem Never opens or changes the database itself; the report has no private content.
rem Close SK Assistant first.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python was not found. Install it from python.org first.
    pause
    exit /b 1
)
%PY% -m puzzle_assistant.devtools.knowledge_audit %*
echo.
pause
