@echo off
cd /d "%~dp0"
rem TEMPORARY SK Brain test bench (development tool, not the SK chat)
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please run INSTALL.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m puzzle_assistant.devtools.brain_bench
