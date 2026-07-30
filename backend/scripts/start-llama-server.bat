@echo off
REM 启动 llama.cpp 服务器（CUDA GPU），提供本地 Qwen3-14B（OpenAI 兼容端点）
REM 端口/别名需与后端 .env 的 LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL 保持一致
cd /d C:\Users\sss208\Desktop\agent\supply-chain-qa

set MODEL_PATH=models\Qwen3-14B-Q4_K_M.gguf
set LLAMA_SERVER=llama.cpp-cuda13\llama-server.exe

if not exist "%MODEL_PATH%" (
    echo ERROR: Model not found at %MODEL_PATH%
    exit /b 1
)

echo Starting llama.cpp server (CUDA) with %MODEL_PATH%...
echo API: http://localhost:18080/v1  (model alias: Qwen3-14B / qwen25)

"%LLAMA_SERVER%" ^
  --model "%MODEL_PATH%" ^
  --host 0.0.0.0 ^
  --port 18080 ^
  --ctx-size 8192 ^
  --batch-size 512 ^
  --n-gpu-layers 99 ^
  --threads 8 ^
  --jinja ^
  --reasoning off ^
  --alias "Qwen3-14B,qwen25"

pause
