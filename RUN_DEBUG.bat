@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please run INSTALL.bat first.
    pause
    exit /b 1
)
echo Running with console output; every analysed puzzle is saved to the debug folder.
".venv\Scripts\python.exe" app.py --debug
pause
