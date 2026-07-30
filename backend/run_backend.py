"""
SupplyChainRAG Backend Launch Script
"""
import subprocess
import sys
import os

from dotenv import load_dotenv

# 切换到脚本所在目录（即 backend/）
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 加载 .env 文件
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

python_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "Scripts", "python.exe")

# 将 .env 中的变量注入环境（load_dotenv 已写入 os.environ）
env = {
    **os.environ,
    "POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("POSTGRES_PORT", "15432"),
    "REDIS_HOST": os.environ.get("REDIS_HOST", "localhost"),
    "REDIS_PORT": os.environ.get("REDIS_PORT", "6379"),
    "MILVUS_HOST": os.environ.get("MILVUS_HOST", "localhost"),
    "MILVUS_PORT": os.environ.get("MILVUS_PORT", "19530"),
    "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "deepseek"),
}

cmd = [
    python_exe, "-m", "uvicorn",
    "app.main:app",
    "--host", "0.0.0.0",
    "--port", "8003",
]

print(f"CMD: {' '.join(cmd)}")
print(f"LLM_PROVIDER={env.get('LLM_PROVIDER')}")

proc = subprocess.Popen(cmd, env=env)
proc.wait()
