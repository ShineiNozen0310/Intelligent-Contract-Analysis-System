@echo off
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found.
  exit /b 1
)

set "PY=.venv\Scripts\python.exe"
set "ICON=%cd%\contract_review_launcher.ico"

echo [check] PyInstaller
"%PY%" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [setup] install pyinstaller...
  "%PY%" -m pip install pyinstaller
  if errorlevel 1 (
    echo [ERROR] install pyinstaller failed.
    exit /b 1
  )
)

if exist "desktop_app\build" rmdir /s /q "desktop_app\build"
if exist "desktop_app\dist\ContractReviewDesktop.exe" del /q "desktop_app\dist\ContractReviewDesktop.exe"
if exist "desktop_app\ContractReviewDesktop.spec" del /q "desktop_app\ContractReviewDesktop.spec"

echo [build] Packaging desktop_app\app_pyside6.py ...
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name ContractReviewDesktop ^
  --icon "%ICON%" ^
  --distpath desktop_app\dist ^
  --workpath desktop_app\build ^
  --specpath desktop_app ^
  desktop_app\app_pyside6.py

if errorlevel 1 (
  echo [ERROR] build failed.
  exit /b 1
)

echo.
echo [OK] Build complete:
echo   %cd%\desktop_app\dist\ContractReviewDesktop.exe
endlocal
