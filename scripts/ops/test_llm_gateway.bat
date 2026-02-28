@echo off
setlocal

curl.exe http://127.0.0.1:4000/v1/chat/completions ^
  -H "Authorization: Bearer sk-gateway-123" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"qwen-main\",\"messages\":[{\"role\":\"user\",\"content\":\"reply OK\"}]}"

set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%
