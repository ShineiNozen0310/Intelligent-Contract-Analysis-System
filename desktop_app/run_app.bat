@echo off
setlocal
cd /d "%~dp0\.."

if /I "%USE_PACKAGED_EXE%"=="1" if exist "desktop_app\dist\ContractReviewDesktop.exe" (
  "desktop_app\dist\ContractReviewDesktop.exe"
  endlocal
  exit /b 0
)

set "PY_EXE=python"
if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"

"%PY_EXE%" desktop_app\app_pyside6.py
if errorlevel 1 (
  echo [warn] PySide6 app failed, fallback to Tk app...
  "%PY_EXE%" desktop_app\app.py
)

endlocal
