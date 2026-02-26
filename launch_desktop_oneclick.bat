@echo off
setlocal

cd /d "%~dp0"

set "APP_EXE=%~dp0desktop_app\dist\ContractReviewDesktop.exe"

if exist "%APP_EXE%" (
  start "" "%APP_EXE%"
) else (
  start "" "%~dp0desktop_app\run_app.bat"
)
endlocal
exit /b 0
