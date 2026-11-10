@echo off
cd /d "%~dp0"
title SK Assistant TRIAL
if not exist ".venv\Scripts\python.exe" (
    echo Run TRIAL_SETUP.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" trial\launcher.py
echo.
echo You can now start your normal SK Assistant again (its usual icon).
pause
