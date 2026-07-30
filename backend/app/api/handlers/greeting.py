"""
GREETING intent handler — 问候/闲聊，直接返回预设回复
"""
import logging
from typing import AsyncGenerator
from app.api.chat_helpers import _handle_greeting, sse_event, sse_done

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ["handle_greeting"]


async def handle_greeting(safe_query: str) -> AsyncGenerator[str, None]:
    """处理问候意图，直接返回预设回复"""
    import time
    _t2 = time.perf_counter()

    answer = _handle_greeting(safe_query)

    _t_gen = time.perf_counter() - _t2
    logger.info(f"[GREETING] 耗时={_t_gen*1000:.0f}ms")

    yield sse_event("content", content=answer)
    yield sse_done()
