@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please run INSTALL.bat first.
    pause
    exit /b 1
)
rem pythonw = no console window. Use RUN_DEBUG.bat to see log output.
start "" ".venv\Scripts\pythonw.exe" app.py %*
