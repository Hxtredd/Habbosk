@echo off
rem Creates Desktop + Start menu shortcuts with the app icon (pin one to the taskbar).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcuts.ps1"
echo.
echo To pin to the taskbar: right-click the "SK Assistant" shortcut
echo (on Windows 11 first "Show more options") and choose "Pin to taskbar".
pause
