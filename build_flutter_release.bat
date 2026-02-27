@echo off
setlocal

cd /d "%~dp0"

set "FLUTTER_CMD=%~dp0tools\flutter\bin\flutter.bat"
if not exist "%FLUTTER_CMD%" (
  where flutter >nul 2>nul
  if errorlevel 1 (
    echo [error] flutter not found in tools\flutter or PATH
    exit /b 1
  )
  set "FLUTTER_CMD=flutter"
)

if "%PUB_HOSTED_URL%"=="" set "PUB_HOSTED_URL=https://mirrors.tuna.tsinghua.edu.cn/dart-pub"
if "%FLUTTER_STORAGE_BASE_URL%"=="" set "FLUTTER_STORAGE_BASE_URL=https://mirrors.tuna.tsinghua.edu.cn/flutter"

cd /d "%~dp0apps\mobile_client_flutter"

if not exist "pubspec.yaml" (
  echo [error] apps\mobile_client_flutter is not a valid Flutter project.
  exit /b 1
)

if not exist "windows\runner\main.cpp" (
  echo [error] Windows platform files are missing.
  echo [hint] run: "%FLUTTER_CMD%" create --platforms=windows .
  exit /b 1
)

echo [build] flutter pub get
call "%FLUTTER_CMD%" pub get
if errorlevel 1 (
  echo [error] flutter pub get failed
  exit /b 1
)

echo [build] flutter build windows --release
call "%FLUTTER_CMD%" build windows --release
if errorlevel 1 (
  echo [error] flutter build windows failed
  exit /b 1
)

set "EXE=%cd%\build\windows\x64\runner\Release\contract_review_flutter.exe"
if not exist "%EXE%" (
  echo [error] release exe not found after build: %EXE%
  exit /b 1
)

echo [ok] build complete: %EXE%

endlocal
exit /b 0
