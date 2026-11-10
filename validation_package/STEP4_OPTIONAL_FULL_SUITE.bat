@echo off
cd /d "%~dp0"
title Step 4 (optional, about 15 minutes) - the whole test suite
echo.
echo  Step 4 (optional, about 15 minutes) - the whole test suite
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
if not exist ".venv-check\Scripts\python.exe" (
  echo.
  echo  First run: setting up a private Python environment in THIS folder ^(.venv-check^).
  echo  It installs only pypdf, the PDF reader the Brain tests need ^(pinned and hash-checked,
  echo  from source\requirements-brain.txt^). This needs the internet once.
  %PY% -m venv .venv-check
  if exist ".venv-check\Scripts\python.exe" (
    ".venv-check\Scripts\python.exe" -m pip install --no-cache-dir --disable-pip-version-check --require-hashes -r source\requirements-brain.txt
  )
)
if exist ".venv-check\Scripts\python.exe" (
  set "PY=.venv-check\Scripts\python.exe"
) else (
  echo  Could not set up .venv-check - continuing with %PY%; PDF tests will fail without pypdf.
)
echo.
%PY% -I -B tools\run_privacy_suite.py --full
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (echo  FINISHED: PASS) else (echo  FINISHED: FAIL - send me the report file named above.)
echo.
pause
exit /b %CODE%
