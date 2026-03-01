@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\release\generate_update_manifest.ps1" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
