@echo off
cd /d "%~dp0"
title SK Assistant TRIAL - full self-test
if not exist ".venv\Scripts\python.exe" (
    echo Run TRIAL_SETUP.bat first.
    pause
    exit /b 1
)
echo Runs every automated test with the isolation guard on (several minutes)...
".venv\Scripts\python.exe" trial\selftest.py --all
pause
