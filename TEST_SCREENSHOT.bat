@echo off
rem Drag a screenshot (PNG/JPG) of the puzzle onto this file to test recognition offline.
cd /d "%~dp0"
if "%~1"=="" (
    echo Drag and drop a screenshot file onto TEST_SCREENSHOT.bat
    pause
    exit /b 1
)
".venv\Scripts\python.exe" app.py --image "%~1"
pause
