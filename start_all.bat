@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\ops\start_all.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
