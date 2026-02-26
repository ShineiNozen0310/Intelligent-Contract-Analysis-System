@echo off
setlocal
cd /d "%~dp0\.."

if exist "desktop_app\dist\ContractReviewDesktop.exe" (
  "desktop_app\dist\ContractReviewDesktop.exe"
  endlocal
  exit /b 0
)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" desktop_app\app_pyside6.py
  if errorlevel 1 (
    echo [warn] PySide6 app failed, fallback to Tk app...
    ".venv\Scripts\python.exe" desktop_app\app.py
  )
) else (
  python desktop_app\app_pyside6.py
  if errorlevel 1 (
    echo [warn] PySide6 app failed, fallback to Tk app...
    python desktop_app\app.py
  )
)

endlocal
