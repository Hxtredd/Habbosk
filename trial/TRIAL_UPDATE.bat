@echo off
rem The whole script is one block: cmd reads it completely before running it,
rem so the update can safely replace this file while it runs.
(
    cd /d "%~dp0"
    title SK Assistant TRIAL - update
    if not exist ".venv\Scripts\python.exe" (
        echo Run TRIAL_SETUP.bat first.
        pause
        exit /b 1
    )
    echo ============================================================
    echo  SK Assistant TRIAL - update to the newest test build
    echo  Only this folder changes. Your settings and data in the trial,
    echo  and your normal SK Assistant, are NOT changed.
    echo ============================================================
    echo.
    ".venv\Scripts\python.exe" trial\update.py
    echo.
    pause
    exit /b
)
