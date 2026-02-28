@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

if not exist runtime mkdir runtime
if not exist runtime\logs mkdir runtime\logs

start "LiteLLM Proxy" cmd /k "litellm --config llm_gateway.yaml --host 127.0.0.1 --port 4000"

endlocal
exit /b 0
