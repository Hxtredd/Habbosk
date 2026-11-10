@echo off
cd /d "%~dp0"
title Step 2 - privacy tests
echo.
echo  Step 2 - privacy tests
echo  ----------------------------------------------------------
echo  This only uses the folder you unzipped. It does not open, copy or
echo  change your everyday SK Assistant, its databases or its settings.
echo.
set "PY="
py -3.14 -c "import sys" >nul 2>nul && set "PY=py -3.14"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
  echo  Could not find Python. Install Python 3.14 from python.org, then run this again.
  pause
  exit /b 2
)
echo  Using: %PY%
echo.
%PY% -I tools\run_privacy_suite.py
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (echo  FINISHED: PASS) else (echo  FINISHED: FAIL - send me the report file named above.)
echo.
pause
exit /b %CODE%
