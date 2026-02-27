@echo off
cd /d "%~dp0"

if not exist runtime mkdir runtime
if not exist runtime\logs mkdir runtime\logs

start "LiteLLM Proxy" cmd /k "litellm --config llm_gateway.yaml --host 127.0.0.1 --port 4000"
