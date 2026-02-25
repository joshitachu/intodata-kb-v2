@echo off
echo Starting Hindsight memory server (embedded mode)...
echo.

set HINDSIGHT_API_LLM_PROVIDER=ollama
set HINDSIGHT_API_LLM_BASE_URL=http://localhost:11434/v1
set HINDSIGHT_API_LLM_MODEL=gemma3:12b

echo Config:
echo   LLM Provider: ollama
echo   LLM Base URL: %HINDSIGHT_API_LLM_BASE_URL%
echo   LLM Model:    %HINDSIGHT_API_LLM_MODEL%
echo   API endpoint: http://127.0.0.1:8888
echo.

python -c "from hindsight_api.main import main; main()" --host 127.0.0.1 --port 8888
