"""
SupplyChainRAG - 配置管理
使用 Pydantic Settings 从 .env 文件加载配置
"""
import os
import logging
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application config, loaded from .env via Pydantic Settings"""

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "..", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    APP_NAME: str = "Supply Chain QA"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False  # 默认关闭，开发环境通过 .env 显式开启
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"

    # ---- 数据库 ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 15432
    POSTGRES_USER: str = "supply_chain_qa"
    POSTGRES_PASSWORD: str = "scqa1234"  # 生产环境必须通过 POSTGRES_PASSWORD 环境变量覆盖
    POSTGRES_DB: str = "supply_chain_qa"

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    REDIS_MAX_CONNECTIONS: int = 50
    REDIS_SOCKET_CONNECT_TIMEOUT: float = 3.0   # 建连超时(秒)
    REDIS_SOCKET_TIMEOUT: float = 5.0           # 单命令超时(秒)，防止 Redis 卡死挂起请求链
    REDIS_HEALTH_CHECK_INTERVAL: int = 30       # 空闲连接自动 PING 保活间隔(秒)
    QUERY_CACHE_TTL: int = 3600                 # Query 缓存过期时间(秒)
    IDEMPOTENT_TTL: int = 300                   # 幂等键保留时间(秒)
    TOOL_LOCK_EXPIRE: int = 15                  # 工具调用分布式锁过期时间(秒)

    # ---- Milvus ----
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION: str = "supply_chain_qa_docs"

    # ---- Embedding ----
    EMBEDDING_MODEL: str = "BAAI/bge-base-zh-v1.5"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DIMENSION: int = 768  # bge-base-zh-v1.5 实际输出 768 维

    # ---- Reranker ----
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_DEVICE: str = "cpu"
    RERANKER_ENABLED: bool = False

    # ---- RAG 参数 ----
    CHUNK_SIZE: int = 256
    CHUNK_OVERLAP: int = 128
    RRF_K: int = 90
    VECTOR_TOP_K: int = 60
    BM25_TOP_K: int = 60
    RERANK_TOP_K: int = 12
    # rerank 分数截断：sigmoid_normalize(rerank_score) 低于该阀值的块被丢弃（精度过滤，保底≥1）。0.0=禁用（默认，不改现状）
    RERANK_SCORE_THRESHOLD: float = 0.0
    # 多查询扇出上限：broad 查询拆分的子问题数上限（越小 avg_ctx/CP分母越低）。5=现状默认
    MAX_SUB_QUERIES: int = 5
    CONFIDENCE_THRESHOLD: float = 0.6
    # RRF 融合分数下限：必须低于最小单路分数 min_weight/RRF_K（当前 1.25/90≈0.0139），
    # 否则仅被单路召回的结果（如纯向量命中）即使排第一也会被过滤（历史 bug：0.015 时向量单路全丢）
    RRF_MIN_SCORE: float = 0.012          # RRF 融合分数下限，低于此分数的结果被过滤
    JACCARD_DEDUP_THRESHOLD: float = 0.7  # 相邻 chunk Jaccard 去重阈值

    # ---- RRF 类型感知权重（可从 .env 覆盖）----
    RRF_BM25_WEIGHT_PRECISE: float = 1.25   # precise 查询中 BM25 路权重
    RRF_VECTOR_WEIGHT_SEMANTIC: float = 2.25 # semantic 查询中向量路权重
    RRF_BM25_WEIGHT_DEFAULT: float = 1.75    # default 查询中 BM25 路权重
    RRF_VECTOR_WEIGHT_DEFAULT: float = 1.25  # default 查询中向量路权重

    # ---- 置信度路由 ----
    CONFIDENCE_LOW: float = 0.3           # 低置信度阈值 → 触发 Web 搜索补充
    CONFIDENCE_HIGH: float = 0.7          # 高置信度阈值 → 直接生成回答

    # ---- 评估阈值 ----
    EVAL_COVERAGE_THRESHOLD: float = 0.25  # 忠实度关键词覆盖率阈值
    EVAL_CONTEXT_RECALL_THRESHOLD: float = 0.3  # 上下文召回覆盖率阈值

    # ---- Memory ----
    MEMORY_WINDOW: int = 10
    MEMORY_TTL: int = 86400
    SUMMARY_INTERVAL: int = 10
    SUMMARY_TRUNCATE_LEN: int = 40

    # ---- 默认角色 ----
    DEFAULT_USER_ROLE: str = "employee"

    # ---- SSE ----
    SSE_HEARTBEAT_INTERVAL: int = 15

    # ---- LLM ----
    LLM_PROVIDER: str = "local"  # 与实际部署一致（本地 llama.cpp）；.env 也显式设为 local

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_FAST_MODEL: str = "deepseek-v4-flash"  # DeepSeek 官方 V4（deepseek-chat 已弃用）

    # MiniMax
    MINIMAX_API_KEY: str = ""
    MINIMAX_BASE_URL: str = "https://api.minimax.chat/v1"
    MINIMAX_MODEL: str = "MiniMax-M2.7"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # 本地 LLM（OpenAI 兼容端点：llama.cpp / Ollama 等）——本地模型接入的单一真相来源
    LOCAL_LLM_BASE_URL: str = "http://localhost:18080/v1"
    LOCAL_LLM_MODEL: str = "Qwen3-14B"
    LOCAL_LLM_API_KEY: str = "local"  # 本地服务通常不校验，占位即可

    # ---- Agent ----
    AGENT_TYPE: str = "react"  # react | langgraph

    # ---- MCP ----
    MCP_SERVERS: str = ""  # comma-separated MCP server URLs

    # ---- Keyword Coverage ----
    COVERAGE_ENABLED: bool = True

    # ---- LLM 相关性过滤（借鉴 Self-RAG 思想，非论文级；实现见 core/llm_relevance.py）----
    LLM_RELEVANCE_ENABLED: bool = True
    LLM_RELEVANCE_THRESHOLD: float = 0.15
    # ---- CRAG (Corrective RAG) ----
    CRAG_ENABLED: bool = True
    CRAG_MAX_RETRIES: int = 1
    CRAG_RELEVANCE_THRESHOLD: float = 0.15


    # ---- 意图路由（规则/语义层，见 intent_routes.py / semantic_router.py）----
    SEMANTIC_ROUTER_THRESHOLD: float = 0.65  # 语义路由相似度阈值（低于此值回退 LLM）
    SEMANTIC_ROUTER_MARGIN: float = 0.03     # top1-top2 意图分差阈值（低于此值视为模糊，回退 LLM）

    # ---- 语义缓存 (L2) ----
    SEMANTIC_CACHE_ENABLED: bool = True
    SEMANTIC_CACHE_SIMILARITY_THRESHOLD: float = 0.92  # 余弦相似度阈值
    SEMANTIC_CACHE_TTL: int = 600                       # 缓存过期时间(秒)
    SEMANTIC_CACHE_MAX_ENTRIES: int = 200               # 最大缓存条目数

    # ---- 多层缓存（cache_manager 统一门面）----
    L1_CACHE_MAX: int = 256          # L1 进程内 LRU 最大条目数
    L1_CACHE_TTL: int = 300          # L1 缓存过期时间(秒)
    L3_CACHE_TTL_SQL: int = 60       # L3 Text-to-SQL 查询结果缓存 TTL(秒)
    L3_CACHE_TTL_TOOL: int = 30      # L3 只读工具查询结果缓存 TTL(秒)

    # ---- 知识库上传 ----
    ALLOW_PUBLIC_UPLOAD: bool = False  # 非 admin 用户是否允许上传 public 可见文档
    MAX_UPLOAD_MB: int = 20            # 单文件上传大小上限(MB)

    # ---- 认证边界 ----
    REQUIRE_AUTH_CHAT: bool = True     # 对话接口是否强制登录（演示环境可关）

    # ---- Demo 模式（面试安全路径）----
    DEMO_MODE: bool = False          # 无LLM时返回本地fallback，不抛 Connection error
    DEMO_SEED_USERS: bool = True     # 启动时自动创建演示账户

    # ---- Langfuse 可观测性 ----
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # ---- JWT 认证 ----
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_SECONDS: int = 86400  # 24小时

    # ---- Neo4j 图数据库（实体关系图谱检索）----
    NEO4J_URI: str = "bolt://localhost:17687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "scqa1234"

    # ---- 图谱融合权重（α=向量+BM25, β=图谱）----
    GRAPH_FUSION_ALPHA: float = 0.5
    GRAPH_FUSION_BETA: float = 0.7
    # 图谱伪 chunk 是否按实体拆分：True=每实体独立过 Critic/独立成 chunk 逐个精排（默认，生产行为）；
    # False=多实体拼成单块整段注入（旧行为，兼作回滚开关）。用于 P1-3 A/B 对照与快速回退。
    GRAPH_CHUNK_SPLIT_BY_ENTITY: bool = True

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

    # ---- 默认密码常量（用于启动校验）----
    _DEFAULT_PASSWORDS = {
        "POSTGRES_PASSWORD": "scqa1234",
        "NEO4J_PASSWORD": "scqa1234",
        "JWT_SECRET": "",
    }

    def validate_security(self) -> None:
        """生产环境安全校验 — DEBUG=False 时拒绝使用默认密码启动

        设计思路（面试谈资）：
        - fail-fast 原则：在启动阶段就阻止不安全的配置，而不是运行时才出问题
        - 分级策略：DEBUG=True 时只 warning，DEBUG=False 时直接 RuntimeError
        - 环境变量覆盖：只要用户设了环境变量覆盖默认值，就通过校验
        """
        violations: list[str] = []

        for name, default_val in self._DEFAULT_PASSWORDS.items():
            current = getattr(self, name, "")
            if current == default_val:
                violations.append(f"{name} 仍为默认值 '{default_val}'")

        # CORS 通配符 + allow_credentials 是危险组合（任意源可携带凭证请求）
        if "*" in self.CORS_ORIGINS_LIST:
            violations.append("CORS_ORIGINS 包含通配符 '*'（生产环境必须指定具体域名）")

        # demo 账号在生产环境存在弱密码风险：不阻断启动，但输出 ERROR 级告警
        if not self.DEBUG and self.DEMO_SEED_USERS:
            logger.error(
                "[SECURITY] 生产环境（DEBUG=False）仍启用 DEMO_SEED_USERS，"
                "将创建弱密码演示账号！请设置 DEMO_SEED_USERS=false 或通过 "
                "DEMO_*_PASSWORD 环境变量覆盖默认密码。"
            )

        if not violations:
            return

        detail = "；".join(violations)
        if self.DEBUG:
            logger.warning(
                f"[SECURITY] 开发环境检测到默认密码（{detail}），"
                "生产部署前请通过环境变量覆盖。"
            )
        else:
            raise RuntimeError(
                f"[SECURITY] 生产环境禁止使用默认密码启动！请通过环境变量覆盖：{detail}。"
                f"设置 DEBUG=True 可跳过此校验（仅限开发环境）。"
            )


@lru_cache()
def get_settings() -> Settings:
    s = Settings()
    if s.DEBUG:
        logger.warning("[CONFIG] DEBUG=True，请确认这是否为开发环境。")
    return s
