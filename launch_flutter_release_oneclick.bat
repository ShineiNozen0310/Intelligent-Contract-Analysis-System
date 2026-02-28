@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\release\launch_flutter_release_oneclick.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
