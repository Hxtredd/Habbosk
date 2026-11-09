@echo off
rem SK AI Speed Check - read-only Gemini timing benchmark. Changes nothing in SK Assistant.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 ai_speed_check.py %*
) else (
    python ai_speed_check.py %*
)
