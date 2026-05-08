"""
SmartQA - 重试装饰器

外部 API 调用（DeepSeek/MiniMax/Milvus）可能临时失败。
指数退避重试是最简单的容错策略。

我用指数退避（2s→4s→8s），最多3次，第3次失败才返回降级回答。
这和 Hermes Agent 的 retry 模式一致。"
"""
import asyncio
import logging
import time
from functools import wraps

logger = logging.getLogger(__name__)


def retry_async(max_attempts: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """异步重试装饰器（指数退避）

    Args:
        max_attempts: 最大尝试次数
        base_delay: 基础延迟（秒），每次翻倍
        exceptions: 需要重试的异常类型
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            f"[Retry] {func.__name__} 第{attempt}次失败: {e}, "
                            f"{delay:.1f}秒后重试..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"[Retry] {func.__name__} 第{attempt}次失败: {e}, 已达最大重试次数"
                        )
            raise last_exception
        return wrapper
    return decorator


def retry_sync(max_attempts: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """同步重试装饰器（指数退避）"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            f"[Retry] {func.__name__} 第{attempt}次失败: {e}, "
                            f"{delay:.1f}秒后重试..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"[Retry] {func.__name__} 第{attempt}次失败: {e}, 已达最大重试次数"
                        )
            raise last_exception
        return wrapper
    return decorator
