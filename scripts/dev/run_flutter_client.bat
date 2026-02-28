@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

set "DEVICE_ARG=%~1"
if /I "%DEVICE_ARG%"=="-h" goto :help
if /I "%DEVICE_ARG%"=="--help" goto :help
if /I "%DEVICE_ARG%"=="help" goto :help

set "FLUTTER_CMD=%REPO_ROOT%\tools\flutter\bin\flutter.bat"
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

if not "%DEVICE_ARG%"=="" (
  set "FLUTTER_DEVICE=%DEVICE_ARG%"
  shift
)
if "%FLUTTER_DEVICE%"=="" set "FLUTTER_DEVICE=windows"

cd /d "%REPO_ROOT%\apps\mobile_client_flutter"

if not exist "pubspec.yaml" (
  echo [error] apps\mobile_client_flutter is not a valid Flutter project.
  exit /b 1
)

if /I "%FLUTTER_DEVICE%"=="windows" (
  if not exist "windows\runner\main.cpp" (
    echo [error] Windows platform files are missing.
    echo [hint] run: "%FLUTTER_CMD%" create --platforms=windows .
    exit /b 1
  )
)

call "%FLUTTER_CMD%" pub get
if errorlevel 1 (
  echo [error] flutter pub get failed
  exit /b 1
)

call "%FLUTTER_CMD%" run -d %FLUTTER_DEVICE% %*

endlocal
exit /b 0

:help
echo Usage: run_flutter_client.bat [device] [extra flutter run args]
echo Example 1: run_flutter_client.bat windows
echo Example 2: run_flutter_client.bat android --debug
echo Example 3: run_flutter_client.bat edge --web-port 8090
exit /b 0
