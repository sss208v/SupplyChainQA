"""
SupplyChainRAG - PostgreSQL数据库连接
"""
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class Base(DeclarativeBase):
    """SQLAlchemy基类"""
    pass


# 异步引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

# 异步Session工厂
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """获取数据库Session（依赖注入）"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """初始化数据库表 — 优先用 Alembic 迁移，回退到 create_all。"""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_cmd
        from pathlib import Path

        alembic_ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
        if alembic_ini.exists():
            alembic_cfg = AlembicConfig(str(alembic_ini))
            # 异步环境下需要在线程中执行 Alembic CLI
            import asyncio
            import functools
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, functools.partial(alembic_cmd.upgrade, alembic_cfg, "head")
            )
            logger.info("Alembic 迁移完成")
            return
    except Exception as e:
        logger.warning(f"Alembic 迁移失败，回退 create_all: {e}")

    # Fallback: 直接 create_all（开发/测试环境）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表初始化完成（create_all fallback）")


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()
    logger.info("数据库连接已关闭")
