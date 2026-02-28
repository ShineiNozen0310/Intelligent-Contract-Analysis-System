@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\ops\stop_all.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
