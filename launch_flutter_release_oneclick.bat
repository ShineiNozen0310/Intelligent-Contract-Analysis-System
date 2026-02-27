@echo off
setlocal

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_flutter_release_oneclick.ps1"
set "EC=%ERRORLEVEL%"

endlocal & exit /b %EC%
