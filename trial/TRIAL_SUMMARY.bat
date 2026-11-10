@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run TRIAL_SETUP.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" trial\summary.py
