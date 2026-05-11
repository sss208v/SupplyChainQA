"""
SmartQA Pro - 企业级智能问答助手系统
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
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import chat, knowledge, tool, feedback, evaluate, auth
from app.core.milvus_client import milvus_manager
from app.core.redis_client import init_redis, close_redis
from app.core.database import init_db, close_db

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 日志层PII脱敏：所有日志输出自动过滤敏感信息（手机号、身份证、姓名等）
from app.core.data_filter import PIILogFilter
logging.getLogger().addFilter(PIILogFilter())

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时初始化所有外部连接，关闭时优雅释放资源。
    如果某个服务连不上，应该明确报错而不是静默失败。
    """
    # ---- 启动阶段 ----
    logger.info("=" * 50)
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")
    logger.info("=" * 50)

    # 1. 连接Milvus
    try:
        milvus_manager.connect()
        milvus_manager.create_collection()
        logger.info("✅ Milvus连接成功")
    except Exception as e:
        logger.warning(f"⚠️ Milvus连接失败: {e}（RAG功能不可用）")

    # 2. 连接Redis
    try:
        await init_redis()
        logger.info("✅ Redis连接成功")
    except Exception as e:
        logger.warning(f"⚠️ Redis连接失败: {e}（对话记忆不可用）")

    # 3. 初始化数据库
    try:
        await init_db()
        logger.info("✅ PostgreSQL连接成功")
    except Exception as e:
        logger.warning(f"⚠️ PostgreSQL连接失败: {e}（元数据功能不可用）")
    # 4. 创建默认用户（RBAC 部门角色）
    try:
        from app.models.user import User, UserRole
        from app.core.auth import hash_password
        from sqlalchemy import select
        from app.core.database import async_session

        # 默认用户列表：username, password, role, department
        default_users = [
            ("admin", "admin123", UserRole.ADMIN, "系统管理"),
            ("purchase", "123456", UserRole.PURCHASE, "采购部"),
            ("warehouse", "123456", UserRole.WAREHOUSE, "仓库部"),
            ("quality", "123456", UserRole.QUALITY, "质量部"),
            ("production", "123456", UserRole.PRODUCTION, "生产部"),
            ("finance", "123456", UserRole.FINANCE, "财务部"),
            ("logistics", "123456", UserRole.LOGISTICS, "物流部"),
        ]

        async with async_session() as session:
            for username, password, role, dept in default_users:
                result = await session.execute(
                    select(User).where(User.username == username)
                )
                if not result.scalar_one_or_none():
                    user = User(
                        username=username,
                        password_hash=hash_password(password),
                        role=role.value,
                        department=dept,
                    )
                    session.add(user)
                    logger.info(f"✅ 创建用户: {username} ({role.value} - {dept})")
            await session.commit()
            logger.info("✅ 默认用户创建完成")
    except Exception as e:
        logger.warning(f"⚠️ 创建默认用户失败: {e}")

    logger.info(f"🎉 {settings.APP_NAME} 启动完成！")
    logger.info(f"📖 API文档: http://localhost:8001/docs")

    # 4. 预热模型（后台加载，不阻塞启动）
    import asyncio
    async def _warmup():
        try:
            from app.core.rag_engine import rag_engine
            logger.info("🔥 预热: 正在加载嵌入模型...")
            await asyncio.get_event_loop().run_in_executor(None, rag_engine.embedding.init)
            logger.info("🔥 预热: 正在加载重排序模型...")
            await asyncio.get_event_loop().run_in_executor(None, rag_engine.reranker.init)
            logger.info("🔥 预热完成！")
        except Exception as e:
            logger.warning(f"⚠️ 预热失败（不影响服务）: {e}")
    asyncio.create_task(_warmup())

    yield  # ← 应用运行期间

    # ---- 关闭阶段 ----
    logger.info("正在关闭服务...")

    await close_db()
    await close_redis()
    milvus_manager.disconnect()

    logger.info("👋 服务已关闭")


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
            count = milvus_manager.get_count()
            health["knowledge_docs_count"] = count
        except Exception:
            pass

    # Redis
    try:
        redis_ok = await redis_manager.client.ping()
        health["services"]["redis"] = {"connected": redis_ok}
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


@app.get("/")
async def root():
    """根路径"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
