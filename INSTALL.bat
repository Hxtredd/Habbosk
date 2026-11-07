@echo off
setlocal
cd /d "%~dp0"
title SK Assistant - Install
echo ============================================================
echo  SK Assistant - installation
echo ============================================================
echo.

rem ---- 1. Python -------------------------------------------------------------
rem Try every Python we can find and keep the first one that really runs and
rem is 3.9+. (The "python" command is often only the Microsoft Store stub.)
set "PY="
call :try_py py -3
if not defined PY call :try_py python
if not defined PY call :try_py python3
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :try_py "%%D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PY call :try_py "%%D\python.exe"
if not defined PY for /d %%D in ("C:\Python3*") do if not defined PY call :try_py "%%D\python.exe"
if not defined PY (
    echo [ERROR] No working Python 3.9 or newer was found.
    echo.
    echo Python installations found on this PC ^(may be empty^):
    py -0p 2>nul
    python --version 2>nul
    echo.
    echo Fix: install Python 3.12 from https://www.python.org/downloads/windows/
    echo      ^(64-bit "Windows installer"^), tick "Add python.exe to PATH",
    echo      then close this window and run INSTALL.bat again.
    echo If "python" opens the Microsoft Store: Windows Settings ^> Apps ^>
    echo      Advanced app settings ^> App execution aliases ^> turn OFF python.exe
    echo      and python3.exe.
    pause
    exit /b 1
)
echo Using Python:
%PY% -c "import sys; print('  ', sys.version.split()[0], sys.executable)"
%PY% -c "import tkinter" 2>nul
if errorlevel 1 (
    echo [ERROR] Your Python has no Tkinter. Re-run the python.org installer, choose
    echo         "Modify" and enable "tcl/tk and IDLE".
    pause
    exit /b 1
)

rem ---- 2. Virtual environment + packages -------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
echo [2/3] Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Package installation failed. Check your internet connection.
    pause
    exit /b 1
)

rem ---- 3. Tesseract OCR engine ------------------------------------------------
echo [3/3] Checking Tesseract OCR...
set "TESS="
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
if not defined TESS (
    for /f "delims=" %%i in ('where tesseract 2^>nul') do if not defined TESS set "TESS=%%i"
)
if not defined TESS (
    echo Tesseract OCR is not installed. Trying to install it with winget...
    where winget >nul 2>nul
    if errorlevel 1 goto :manual_tess
    winget install -e --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
    if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
    if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
)
if not defined TESS goto :manual_tess
echo Found Tesseract: "%TESS%"

rem Spanish language model (bundled; downloaded only if missing)
if not exist "tessdata\spa.traineddata" (
    echo Downloading Spanish OCR model...
    if not exist tessdata mkdir tessdata
    powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/spa.traineddata' -OutFile 'tessdata\spa.traineddata'"
)
if not exist "tessdata\spa.traineddata" (
    echo [WARNING] Spanish model missing. Install Tesseract with the Spanish language option.
)

echo.
echo Self-test (no OCR needed)...
".venv\Scripts\python.exe" -m unittest discover -s tests
echo.
echo Creating Desktop and Start menu shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcuts.ps1"
echo.
echo ============================================================
echo  Installation finished. Start the program with the
echo  "SK Assistant" icon on your Desktop (or RUN.bat).
echo  To pin it: right-click the icon ^> Pin to taskbar
echo  (Windows 11: right-click ^> Show more options ^> Pin to taskbar).
echo ============================================================
pause
exit /b 0

:manual_tess
echo.
echo [ACTION NEEDED] Please install Tesseract OCR manually:
echo   1. Open https://github.com/UB-Mannheim/tesseract/wiki
echo   2. Download and run the 64-bit installer (default folder).
echo   3. Run INSTALL.bat again.
echo If you install it somewhere else, put the full path of tesseract.exe
echo into "tesseract_cmd" in config.json.
pause
exit /b 1

:try_py
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0
