"""
SmartQA Pro - 配置管理
使用 Pydantic Settings 从 .env 文件加载配置
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""

    # ---- 应用 ----
    APP_NAME: str = "SmartQA"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"

    # ---- 数据库 ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "smartqa"
    POSTGRES_PASSWORD: str = "smartqa123"
    POSTGRES_DB: str = "smartqa"

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    # ---- Milvus ----
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION: str = "smartqa_docs"

    # ---- Embedding ----
    EMBEDDING_MODEL: str = "BAAI/bge-small-zh-v1.5"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DIMENSION: int = 512

    # ---- Reranker ----
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_DEVICE: str = "cpu"
    RERANKER_ENABLED: bool = False

    # ---- RAG 参数 ----
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    VECTOR_TOP_K: int = 20
    BM25_TOP_K: int = 20
    RERANK_TOP_K: int = 3
    CONFIDENCE_THRESHOLD: float = 0.65

    # ---- Memory ----
    MEMORY_WINDOW: int = 10
    MEMORY_TTL: int = 86400
    SUMMARY_INTERVAL: int = 10
    SUMMARY_TRUNCATE_LEN: int = 40

    # ---- SSE ----
    SSE_HEARTBEAT_INTERVAL: int = 15

    # ---- LLM ----
    LLM_PROVIDER: str = "deepseek"

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # MiniMax
    MINIMAX_API_KEY: str = ""
    MINIMAX_BASE_URL: str = "https://api.minimax.chat/v1"
    MINIMAX_MODEL: str = "MiniMax-M2.7"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # ---- Agent ----
    AGENT_TYPE: str = "react"  # react / langchain

    # ---- Guardrails ----
    GUARDRAILS_ENABLED: bool = True

    # ---- Faithfulness ----
    FAITHFULNESS_ENABLED: bool = True

    # ---- Self-RAG ----
    SELF_RAG_ENABLED: bool = True

    class Config:
        env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def REDIS_URL(self) -> str:
        pwd = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{pwd}{self.REDIS_HOST}:{self.REDIS_PORT}"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
