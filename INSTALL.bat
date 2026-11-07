@echo off
setlocal
cd /d "%~dp0"
title Puzzle Answer Assistant - Install
echo ============================================================
echo  Puzzle Answer Assistant - installation
echo ============================================================
echo.

rem ---- 1. Python -------------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 3 was not found.
    echo         Install it from https://www.python.org/downloads/windows/
    echo         and tick "Add python.exe to PATH" during setup. Then run INSTALL.bat again.
    pause
    exit /b 1
)
%PY% -c "import sys; assert sys.version_info >= (3, 9)" 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.9 or newer is required.
    pause
    exit /b 1
)
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
echo ============================================================
echo  Installation finished. Start the program with RUN.bat
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
