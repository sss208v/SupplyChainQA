"""
SmartQA Pro - 配置管理
使用 Pydantic Settings 从 .env 文件加载配置
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application config, loaded from .env via Pydantic Settings"""

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "..", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    APP_NAME: str = "SmartQA"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"

    # ---- 数据库 ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 15432
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
    RRF_K: int = 60
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
    DEEPSEEK_FAST_MODEL: str = "deepseek-chat"  # 快速模型（与主模型相同，DeepSeek 无独立 fast 型号时回退）

    # MiniMax
    MINIMAX_API_KEY: str = ""
    MINIMAX_BASE_URL: str = "https://api.minimax.chat/v1"
    MINIMAX_MODEL: str = "MiniMax-M2.7"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # ---- Agent ----

    # ---- 内容过滤（已禁用）----
    GUARDRAILS_ENABLED: bool = False

    # ---- Faithfulness ----
    FAITHFULNESS_ENABLED: bool = True

    # ---- Self-RAG ----
    SELF_RAG_ENABLED: bool = True

    # ---- CLIP 多模态嵌入（图文混合检索）----
    CLIP_ENABLED: bool = False  # 默认关闭，避免 HuggingFace 下载阻塞演示
    CLIP_MODEL: str = "openai/clip-vit-base-patch32"
    CLIP_IMAGE_COLLECTION: str = "smartqa_images"
    CLIP_TOP_K: int = 3
    CLIP_DEVICE: str = "cpu"

    # ---- Vision API（已弃用，文件保留）----
    VISION_ENABLED: bool = False

    # ---- Neo4j 图数据库（实体关系图谱检索）----
    NEO4J_URI: str = "bolt://localhost:17687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "smartqa123"

    # ---- 图谱融合权重（α=向量+BM25, β=图谱）----
    GRAPH_FUSION_ALPHA: float = 0.7
    GRAPH_FUSION_BETA: float = 0.3

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def REDIS_URL(self) -> str:
        pwd = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{pwd}{self.REDIS_HOST}:{self.REDIS_PORT}"

    @property
    def CORS_ORIGINS_LIST(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
