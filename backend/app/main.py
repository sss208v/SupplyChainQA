"""
SupplyChainRAG - 企业级智能问答助手系统
============================================================

1. lifespan（生命周期）是FastAPI 0.93+引入的新特性
   替代了旧的on_event("startup")/on_event("shutdown")
   优点：使用async with上下文管理器，更安全、更Pythonic

2. 启动顺序（重要！有依赖关系）：
   Milvus连接 → Redis连接 → 数据库初始化
   Milvus必须先连上，RAG引擎才能创建Collection

3. 关闭顺序（反向）：
   数据库关闭 → Redis关闭 → Milvus断开

4. CORS中间件：
   前后端分离项目必须配置CORS，否则浏览器会阻止跨域请求
   allow_origins=["*"] 仅开发环境使用，生产环境需指定具体域名
============================================================
"""
import warnings
warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module="langgraph")

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import chat, knowledge, tool, feedback, evaluate, auth
from app.core.milvus_client import milvus_manager
from app.core.redis_client import init_redis, close_redis
from app.core.database import init_db, close_db
from app.core.neo4j_client import neo4j_client
from app.models.user import User, UserRole
from app.core.auth import hash_password

logger = logging.getLogger(__name__)
settings = get_settings()

# 配置日志：DEBUG 模式用人类可读格式，生产环境用 JSON 格式
from app.core.structured_logging import setup_logging  # noqa: E402
setup_logging(debug=settings.DEBUG)

# 日志层PII脱敏：所有日志输出自动过滤敏感信息（手机号、身份证、姓名等）
from app.core.data_filter import PIILogFilter
logging.getLogger().addFilter(PIILogFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时初始化所有外部连接，关闭时优雅释放资源。
    如果某个服务连不上，应该明确报错而不是静默失败。
    """
    # ---- 启动阶段 ----
    # 安全校验：DEBUG=False 时拒绝默认密码（fail-fast）
    settings.validate_security()

    logger.info("=" * 50)
    logger.info(f"[启动] {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")
    logger.info("=" * 50)

    # 1. 连接Milvus
    try:
        milvus_manager.connect()
        milvus_manager.create_collection()
        milvus_manager.ensure_loaded()  # 启动时预加载一次，避免每次检索重复 load
        logger.info("[OK] Milvus连接成功")
    except Exception as e:
        logger.warning(f"[警告] Milvus连接失败: {e}（RAG功能不可用）")

    # 2. 连接Redis
    try:
        await init_redis()
        logger.info("[OK] Redis连接成功")
    except Exception as e:
        logger.warning(f"[警告] Redis连接失败: {e}（对话记忆不可用）")

    # 3. 初始化数据库
    try:
        await init_db()
        logger.info("[OK] PostgreSQL连接成功")
    except Exception as e:
        logger.warning(f"[警告] PostgreSQL连接失败: {e}（元数据功能不可用）")
    # 4. 创建默认用户（受 DEMO_SEED_USERS 控制，仅用于演示环境）
    # 默认用户列表 — 格式: (用户名, 密码, 角色, 部门)
    # 密码优先从环境变量读取，仅作演示 fallback（与前端硬编码一致）
    import os as _os
    default_users = [
        ("admin", _os.getenv("DEMO_ADMIN_PASSWORD", "admin123"), UserRole.ADMIN, "管理部"),
        ("purchase", _os.getenv("DEMO_PURCHASE_PASSWORD", "purchase123"), UserRole.PURCHASE, "采购部"),
        ("warehouse", _os.getenv("DEMO_WAREHOUSE_PASSWORD", "warehouse123"), UserRole.WAREHOUSE, "仓库部"),
        ("quality", _os.getenv("DEMO_QUALITY_PASSWORD", "quality123"), UserRole.QUALITY, "质量部"),
        ("production", _os.getenv("DEMO_PRODUCTION_PASSWORD", "production123"), UserRole.PRODUCTION, "生产部"),
        ("finance", _os.getenv("DEMO_FINANCE_PASSWORD", "finance123"), UserRole.FINANCE, "财务部"),
        ("logistics", _os.getenv("DEMO_LOGISTICS_PASSWORD", "logistics123"), UserRole.LOGISTICS, "物流部"),
    ]

    if settings.DEMO_SEED_USERS:
        try:
            import asyncio as _asyncio
            from sqlalchemy import select
            from app.core.database import async_session

            async def _seed_users():
                async with _asyncio.timeout(10):
                    async with async_session() as session:
                        for username, password, role, dept in default_users:
                            result = await session.execute(
                                select(User).where(User.username == username)
                            )
                            existing = result.scalar_one_or_none()
                            if existing:
                                # 确密码哈希正确（修复损坏的哈希）
                                from app.core.auth import verify_password
                                if not verify_password(password, existing.password_hash):
                                    existing.password_hash = hash_password(password)
                                    existing.role = role.value
                                    existing.department = dept
                            else:
                                user = User(
                                    username=username,
                                    password_hash=hash_password(password),
                                    role=role.value,
                                    department=dept,
                                )
                                session.add(user)
                        await session.commit()

            # 直接在 lifespan async 上下文中执行（避免线程竞态）
            await _seed_users()
            logger.info("[OK] 默认用户初始化完成")
        except Exception as e:
            logger.warning(f"[警告] 创建默认用户失败: {e}")
    else:
        logger.info("ℹ️ DEMO_SEED_USERS=false，跳过默认账号创建")

    # 5. 连接 Neo4j 并同步图谱数据
    try:
        if await neo4j_client.connect():
            result = await neo4j_client.sync_from_sqlite()
            if result.get("synced"):
                logger.info("[OK] Neo4j 连接成功，图谱同步完成")
            else:
                logger.warning("[警告] 图谱同步失败: %s", result.get("reason", "未知"))
        else:
            logger.warning("[警告] Neo4j 未连接（图谱检索不可用）")
    except Exception as e:
        logger.warning("[警告] Neo4j 初始化失败: %s（图谱检索不可用）", e)

    # 6. 从 Milvus 重建 BM25 索引（启动时自动恢复）
    try:
        from collections import defaultdict
        from app.core.rag_engine import rag_engine as _rag
        c = milvus_manager.collection
        c.load()
        all_chunks = []
        offset = 0
        batch_size = 5000
        while True:
            batch = c.query(expr="id > 0", output_fields=["doc_id", "chunk_id", "content", "source", "page_num", "security_group"], limit=batch_size, offset=offset)
            if not batch:
                break
            all_chunks.extend(batch)
            offset += batch_size
            if len(batch) < batch_size:
                break
        doc_chunks = defaultdict(list)
        for r in all_chunks:
            doc_chunks[r["doc_id"]].append({
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "source": r["source"],
                "page_num": r.get("page_num", 0),
                "security_group": r.get("security_group", ["admin"]),
            })
        for doc_id, chunks in doc_chunks.items():
            sg = chunks[0].get("security_group", ["admin"])
            _rag.bm25.index_documents(doc_id, chunks, security_group=sg)
        logger.info("[OK] BM25 索引重建完成: %d chunks, %d docs", len(all_chunks), len(doc_chunks))
    except Exception as e:
        logger.warning("[警告] BM25 索引重建失败: %s", e)

    logger.info(f" {settings.APP_NAME} 启动完成！")
    logger.info(" API文档: http://localhost:8001/docs")

    # 4. 预热模型（跳过——首次请求时懒加载）
    # 注：warmup 需要多次 HuggingFace HEAD 请求验证缓存，网络不好时可能很慢
    # 模型会在首次使用时自动初始化
    logger.info(" 预热: 跳过（模型首次使用时懒加载）")

    yield  # ← 应用运行期间

    # ---- 关闭阶段 ----
    logger.info("正在关闭服务...")

    await close_db()
    await close_redis()
    await neo4j_client.disconnect()
    milvus_manager.disconnect()

    logger.info(" 服务已关闭")


# ---- 创建FastAPI应用 ----
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="企业级智能问答助手系统 - 基于RAG + Multi-Agent架构",
    lifespan=lifespan,
)

# ---- CORS中间件 ----
# CORS = Cross-Origin Resource Sharing（跨域资源共享）
# 浏览器的同源策略会阻止前端(localhost:5173)访问后端(localhost:8000)
# 配置CORS后，浏览器允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=True,
    allow_methods=["*"],          # 允许所有HTTP方法
    allow_headers=["*"],          # 允许所有请求头
)

# ---- 限流中间件 ----
from app.core.rate_limiter import RateLimitMiddleware  # noqa: E402
from app.core.redis_client import redis_manager  # noqa: E402
app.add_middleware(RateLimitMiddleware, redis_client=redis_manager)

# ---- 注册API路由 ----
app.include_router(chat.router, prefix=settings.API_PREFIX)
app.include_router(knowledge.router, prefix=settings.API_PREFIX)
app.include_router(tool.router, prefix=settings.API_PREFIX)
app.include_router(feedback.router, prefix=settings.API_PREFIX)
app.include_router(evaluate.router, prefix=settings.API_PREFIX)
app.include_router(auth.router, prefix=settings.API_PREFIX)


# ---- 健康检查（全链路） ----
@app.get("/health")
async def health_check():
    """服务健康检查（全链路）"""
    from app.core.redis_client import redis_manager
    from app.core.database import engine as async_engine

    health = {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "embedding_model": settings.EMBEDDING_MODEL,
        "reranker_enabled": settings.RERANKER_ENABLED,
        "agent_type": getattr(settings, "AGENT_TYPE", "react"),
        "knowledge_docs_count": 0,
        "knowledge_chunks_count": 0,
        "services": {}
    }

    # Milvus
    milvus_connected = milvus_manager.is_connected
    health["services"]["milvus"] = {
        "connected": milvus_connected,
        "host": f"{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    }
    if milvus_connected:
        try:
            health["knowledge_chunks_count"] = milvus_manager.get_count()
            health["knowledge_docs_count"] = milvus_manager.get_distinct_doc_count()
        except Exception as e:
            logger.debug(f"[Health] Milvus计数获取失败: {e}")

    # Redis
    try:
        import time as _time
        _t0 = _time.perf_counter()
        redis_ok = await redis_manager.client.ping()
        latency_ms = round((_time.perf_counter() - _t0) * 1000, 1)
        info_mem = await redis_manager.client.info(section="memory")
        info_stats = await redis_manager.client.info(section="stats")
        hits = info_stats.get("keyspace_hits", 0)
        misses = info_stats.get("keyspace_misses", 0)
        health["services"]["redis"] = {
            "connected": bool(redis_ok),
            "latency_ms": latency_ms,
            "used_memory_human": info_mem.get("used_memory_human"),
            "hit_rate": round(hits / (hits + misses), 3) if (hits + misses) else None,
        }
    except Exception:
        health["services"]["redis"] = {"connected": False}

    # PostgreSQL
    try:
        import sqlalchemy
        async with async_engine.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT 1"))
        health["services"]["postgres"] = {"connected": True}
    except Exception:
        health["services"]["postgres"] = {"connected": False}

    # Neo4j
    try:
        neo4j_health = await neo4j_client.health()
        health["services"]["neo4j"] = neo4j_health
    except Exception:
        health["services"]["neo4j"] = {"connected": False}

    # Overall status
    all_ok = all(s.get("connected") for s in health["services"].values())
    health["status"] = "ok" if all_ok else "degraded"

    return health


@app.get("/config")
async def get_config():
    """返回当前配置（脱敏）"""
    return {
        "llm_provider": settings.LLM_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_dimension": settings.EMBEDDING_DIMENSION,
        "reranker_model": settings.RERANKER_MODEL,
        "chunk_size": settings.CHUNK_SIZE,
        "chunk_overlap": settings.CHUNK_OVERLAP,
        "vector_top_k": settings.VECTOR_TOP_K,
        "rerank_top_k": settings.RERANK_TOP_K,
        "confidence_threshold": settings.CONFIDENCE_THRESHOLD,
        "memory_window": settings.MEMORY_WINDOW,
    }


@app.post("/admin/reranker/enable")
async def enable_reranker(request: Request):
    """Runtime 启用重排序模型（避免启动时加载导致 Windows 崩溃）

    首次调用需 20-40 秒加载 2.1GB 模型，后续调用秒回。
    需要 admin 角色权限。
    """
    from app.core.auth import get_current_user_full, check_role
    current_user = await get_current_user_full(request)
    check_role(current_user, ["admin"])
    from app.core.rag_engine import rag_engine
    import time

    if rag_engine.reranker._model is not None:
        return {"status": "ok", "message": "重排序模型已在运行", "already_loaded": True}

    t0 = time.time()
    try:
        rag_engine.reranker.init()
        elapsed = time.time() - t0
        return {
            "status": "ok",
            "message": f"重排序模型加载完成，耗时 {elapsed:.1f}s",
            "elapsed_seconds": round(elapsed, 1),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"加载失败: {str(e)}",
        }


@app.get("/")
async def root():
    """根路径"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
