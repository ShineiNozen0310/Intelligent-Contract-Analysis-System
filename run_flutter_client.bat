@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\dev\run_flutter_client.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
