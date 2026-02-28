@echo off
setlocal
cd /d "%~dp0"
call "%~dp0scripts\ops\stop_llm_gateway.bat" %*
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
