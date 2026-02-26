@echo off
setlocal

cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "DJANGO_TITLE=DjangoProject1-Django"
set "WORKER_TITLE=DjangoProject1-Worker"
set "CELERY_TITLE=DjangoProject1-Celery"

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

if /I "%ACTION%"=="start" goto :do_start
if /I "%ACTION%"=="stop" goto :do_stop
if /I "%ACTION%"=="restart" goto :do_restart
if /I "%ACTION%"=="status" goto :do_status
if /I "%ACTION%"=="help" goto :help

echo [ERROR] unknown action: %ACTION%
goto :help

:do_start
if not exist "%PY%" (
  echo [ERROR] .venv not found. Please create venv first.
  exit /b 1
)

call "%~f0" stop >nul 2>nul
timeout /t 1 >nul

echo [start] %WORKER_TITLE%
start "%WORKER_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" -m uvicorn contract_review_worker.api.main:app --host 127.0.0.1 --port 8001"

echo [start] %CELERY_TITLE%
start "%CELERY_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" -m celery -A contract_review_worker.celery_app worker -l info -P solo"

echo [start] %DJANGO_TITLE%
start "%DJANGO_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000 --noreload"

echo [ok] services start command sent.
goto :end

:do_stop
echo [stop] %WORKER_TITLE%
taskkill /F /FI "WINDOWTITLE eq %WORKER_TITLE%" >nul 2>nul
echo [stop] %CELERY_TITLE%
taskkill /F /FI "WINDOWTITLE eq %CELERY_TITLE%" >nul 2>nul
echo [stop] %DJANGO_TITLE%
taskkill /F /FI "WINDOWTITLE eq %DJANGO_TITLE%" >nul 2>nul
echo [ok] services stop command sent.
goto :end

:do_restart
call "%~f0" stop
timeout /t 1 >nul
call "%~f0" start
goto :end

:do_status
tasklist /v /fi "WINDOWTITLE eq %WORKER_TITLE%" | findstr /I /C:"%WORKER_TITLE%" >nul
if errorlevel 1 (echo [stopped] %WORKER_TITLE%) else (echo [running] %WORKER_TITLE%)

tasklist /v /fi "WINDOWTITLE eq %CELERY_TITLE%" | findstr /I /C:"%CELERY_TITLE%" >nul
if errorlevel 1 (echo [stopped] %CELERY_TITLE%) else (echo [running] %CELERY_TITLE%)

tasklist /v /fi "WINDOWTITLE eq %DJANGO_TITLE%" | findstr /I /C:"%DJANGO_TITLE%" >nul
if errorlevel 1 (echo [stopped] %DJANGO_TITLE%) else (echo [running] %DJANGO_TITLE%)

goto :end

:help
echo Usage: start_all.bat [start^|stop^|restart^|status^|help]
goto :end

:end
endlocal
