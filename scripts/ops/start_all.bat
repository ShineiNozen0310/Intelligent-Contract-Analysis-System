@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if not "%%~A"=="" set "%%~A=%%~B"
  )
)

set "PY=.venv\Scripts\python.exe"
set "DJANGO_TITLE=DjangoProject1-Django"
set "WORKER_TITLE=DjangoProject1-Worker"
set "CELERY_TITLE=DjangoProject1-Celery"
set "VLLM_TITLE=DjangoProject1-vLLM"
set "LOCAL_API_TITLE=DjangoProject1-LocalAPI"
set "VLLM_PY=%PY%"
set "DJANGO_HEALTH_URL=http://127.0.0.1:8000/contract/api/health/"
set "WORKER_HEALTH_URL=http://127.0.0.1:8001/healthz"

if not defined LOCAL_API_PORT set "LOCAL_API_PORT=8003"
set "LOCAL_API_HEALTH_URL=http://127.0.0.1:%LOCAL_API_PORT%/contract/api/health/"

if not defined LOCAL_VLLM_HOST set "LOCAL_VLLM_HOST=127.0.0.1"
if not defined LOCAL_VLLM_PORT set "LOCAL_VLLM_PORT=8002"
if not defined LOCAL_VLLM_MODEL set "LOCAL_VLLM_MODEL=.\hf_models\Qwen3-8B-AWQ"
if not defined LOCAL_VLLM_SERVED_MODEL set "LOCAL_VLLM_SERVED_MODEL=%LOCAL_VLLM_MODEL%"
if not defined LOCAL_VLLM_API_KEY set "LOCAL_VLLM_API_KEY=dummy"
if not defined VLLM_MAX_MODEL_LEN set "VLLM_MAX_MODEL_LEN=4096"
if not defined LOCAL_VLLM_EXTRA_ARGS set "LOCAL_VLLM_EXTRA_ARGS=--quantization awq_marlin --dtype half --gpu-memory-utilization 0.86 --max-model-len 256 --max-num-seqs 1 --enforce-eager"
if defined LOCAL_VLLM_PYTHON set "VLLM_PY=%LOCAL_VLLM_PYTHON%"

if not defined LLM_PRIMARY_PROVIDER set "LLM_PRIMARY_PROVIDER=%LLM_PROVIDER%"
if not defined LLM_REQUIRE_LOCAL_VLLM (
  if /I "%LLM_PRIMARY_PROVIDER%"=="local_vllm" (
    set "LLM_REQUIRE_LOCAL_VLLM=1"
  ) else if /I "%LLM_PRIMARY_PROVIDER%"=="local" (
    set "LLM_REQUIRE_LOCAL_VLLM=1"
  ) else if /I "%LLM_PRIMARY_PROVIDER%"=="vllm" (
    set "LLM_REQUIRE_LOCAL_VLLM=1"
  ) else (
    set "LLM_REQUIRE_LOCAL_VLLM=0"
  )
)

if not defined VLLM_ENABLED set "VLLM_ENABLED=0"
if /I "%LLM_PROVIDER%"=="local_vllm" set "VLLM_ENABLED=1"
if /I "%LLM_PROVIDER%"=="local" set "VLLM_ENABLED=1"
if /I "%LLM_PROVIDER%"=="vllm" set "VLLM_ENABLED=1"
if /I "%LLM_PRIMARY_PROVIDER%"=="local_vllm" set "VLLM_ENABLED=1"
if /I "%LLM_PRIMARY_PROVIDER%"=="local" set "VLLM_ENABLED=1"
if /I "%LLM_PRIMARY_PROVIDER%"=="vllm" set "VLLM_ENABLED=1"
if /I "%LLM_REQUIRE_LOCAL_VLLM%"=="1" set "VLLM_ENABLED=1"

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

echo [llm] route provider=%LLM_PROVIDER% primary=%LLM_PRIMARY_PROVIDER% vllm_enabled=%VLLM_ENABLED% require_local=%LLM_REQUIRE_LOCAL_VLLM%

if /I "%VLLM_ENABLED%"=="1" (
  call :port_listening %LOCAL_VLLM_PORT%
  if not errorlevel 1 (
    echo [reuse] %VLLM_TITLE% endpoint already listening at %LOCAL_VLLM_HOST%:%LOCAL_VLLM_PORT%
  ) else (
    if defined LOCAL_VLLM_START_CMD (
      echo [start] %VLLM_TITLE% ^(LOCAL_VLLM_START_CMD^)
      start "%VLLM_TITLE%" cmd /k "%LOCAL_VLLM_START_CMD%"
    ) else (
      "%VLLM_PY%" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('vllm') else 1)"
      if errorlevel 1 (
        if /I "%LLM_REQUIRE_LOCAL_VLLM%"=="1" (
          echo [error] local vLLM is required but package not found in %VLLM_PY%.
          echo [error] set LOCAL_VLLM_PYTHON to a python with vllm, or set LOCAL_VLLM_START_CMD.
          exit /b 1
        ) else (
          echo [warn] vllm package not found in %VLLM_PY%, skip local vLLM startup.
          if /I not "%LLM_LOCAL_FALLBACK_REMOTE%"=="1" (
            echo [warn] LLM_LOCAL_FALLBACK_REMOTE is disabled. LLM requests may fail.
          )
        )
      ) else (
        echo [start] %VLLM_TITLE%
        start "%VLLM_TITLE%" cmd /k ""%VLLM_PY%" -m vllm serve "%LOCAL_VLLM_MODEL%" --host %LOCAL_VLLM_HOST% --port %LOCAL_VLLM_PORT% --served-model-name "%LOCAL_VLLM_SERVED_MODEL%" --api-key "%LOCAL_VLLM_API_KEY%" %LOCAL_VLLM_EXTRA_ARGS%"
      )
    )
  )
) else (
  echo [skip] %VLLM_TITLE% disabled ^(set VLLM_ENABLED=1 or LLM_PROVIDER=local_vllm to enable^)
)

call :http_ok "%WORKER_HEALTH_URL%"
if not errorlevel 1 (
  echo [reuse] %WORKER_TITLE% already healthy at 127.0.0.1:8001
) else (
  echo [start] %WORKER_TITLE%
  start "%WORKER_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" -m uvicorn contract_review_worker.api.main:app --host 127.0.0.1 --port 8001"
)

powershell -NoProfile -Command ^
  "$found = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -and $_.CommandLine -like '*-m celery*contract_review_worker.celery_app*worker*' }; if($found) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  echo [reuse] %CELERY_TITLE% already running
) else (
  echo [start] %CELERY_TITLE%
  start "%CELERY_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" -m celery -A contract_review_worker.celery_app worker -l info -P solo"
)

call :http_ok "%DJANGO_HEALTH_URL%"
if not errorlevel 1 (
  echo [reuse] %DJANGO_TITLE% already healthy at 127.0.0.1:8000
) else (
  echo [start] %DJANGO_TITLE%
  start "%DJANGO_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000 --noreload"
)

call :port_listening %LOCAL_API_PORT%
if not errorlevel 1 (
  echo [reuse] %LOCAL_API_TITLE% already listening at 127.0.0.1:%LOCAL_API_PORT%
) else (
  echo [start] %LOCAL_API_TITLE%
  start "%LOCAL_API_TITLE%" cmd /k ""%cd%\.venv\Scripts\python.exe" -m uvicorn apps.local_api.main:app --host 127.0.0.1 --port %LOCAL_API_PORT%"
)

echo [ok] services start command sent.
goto :end

:do_stop
echo [stop] %VLLM_TITLE%
taskkill /F /FI "WINDOWTITLE eq %VLLM_TITLE%" >nul 2>nul
call :kill_port %LOCAL_VLLM_PORT%
echo [stop] %LOCAL_API_TITLE%
taskkill /F /FI "WINDOWTITLE eq %LOCAL_API_TITLE%" >nul 2>nul
call :kill_port %LOCAL_API_PORT%
echo [stop] %WORKER_TITLE%
taskkill /F /FI "WINDOWTITLE eq %WORKER_TITLE%" >nul 2>nul
call :kill_port 8001
echo [stop] %CELERY_TITLE%
taskkill /F /FI "WINDOWTITLE eq %CELERY_TITLE%" >nul 2>nul
call :kill_celery
echo [stop] %DJANGO_TITLE%
taskkill /F /FI "WINDOWTITLE eq %DJANGO_TITLE%" >nul 2>nul
call :kill_port 8000
echo [ok] services stop command sent.
goto :end

:do_restart
call "%~f0" stop
timeout /t 1 >nul
call "%~f0" start
goto :end

:do_status
call :port_listening %LOCAL_VLLM_PORT%
if errorlevel 1 (echo [stopped] %VLLM_TITLE%) else (echo [running] %VLLM_TITLE%)

call :http_ok "%WORKER_HEALTH_URL%"
if errorlevel 1 (echo [stopped] %WORKER_TITLE%) else (echo [running] %WORKER_TITLE%)

powershell -NoProfile -Command ^
  "$found = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -and $_.CommandLine -like '*-m celery*contract_review_worker.celery_app*worker*' }; if($found) { exit 0 } else { exit 1 }"
if errorlevel 1 (echo [stopped] %CELERY_TITLE%) else (echo [running] %CELERY_TITLE%)

call :http_ok "%DJANGO_HEALTH_URL%"
if errorlevel 1 (echo [stopped] %DJANGO_TITLE%) else (echo [running] %DJANGO_TITLE%)

call :port_listening %LOCAL_API_PORT%
if errorlevel 1 (echo [stopped] %LOCAL_API_TITLE%) else (echo [running] %LOCAL_API_TITLE%)

goto :end

:help
echo Usage: start_all.bat [start^|stop^|restart^|status^|help]
goto :end

:http_ok
powershell -NoProfile -Command ^
  "$ProgressPreference='SilentlyContinue'; try { $r = Invoke-WebRequest -UseBasicParsing '%~1' -TimeoutSec 2; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) { exit 0 } else { exit 1 } } catch { exit 1 }"
exit /b %errorlevel%

:port_listening
powershell -NoProfile -Command ^
  "try { $c = New-Object System.Net.Sockets.TcpClient; $iar = $c.BeginConnect('127.0.0.1', %~1, $null, $null); if($iar.AsyncWaitHandle.WaitOne(800)) { $c.EndConnect($iar); $c.Close(); exit 0 } else { $c.Close(); exit 1 } } catch { exit 1 }"
exit /b %errorlevel%

:kill_port
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%~1 .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>nul
)
exit /b 0

:kill_celery
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -and $_.CommandLine -like '*-m celery*contract_review_worker.celery_app*worker*' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
exit /b 0

:end
endlocal
