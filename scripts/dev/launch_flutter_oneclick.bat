@echo off
setlocal
if /I "%~1"=="-h" goto :help
if /I "%~1"=="--help" goto :help
if /I "%~1"=="help" goto :help
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_flutter_oneclick.ps1" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%

:help
call "%~dp0run_flutter_client.bat" --help
endlocal & exit /b 0
