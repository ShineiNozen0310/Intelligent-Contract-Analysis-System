@echo off
setlocal

cd /d "%~dp0"

call "%~dp0start_all.bat" start
if errorlevel 1 (
  echo [error] backend startup failed
  exit /b 1
)

call "%~dp0run_flutter_client.bat"

endlocal
exit /b 0
