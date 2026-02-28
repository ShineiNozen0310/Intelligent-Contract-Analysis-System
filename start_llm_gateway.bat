@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\ops\start_llm_gateway.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
