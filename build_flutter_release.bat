@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\release\build_flutter_release.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
