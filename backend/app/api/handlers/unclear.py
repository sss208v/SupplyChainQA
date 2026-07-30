"""
UNCLEAR intent handler — 意图不明确时的兜底处理

先用轻量检索，搜到则回复，没搜到则给引导建议。
"""
import asyncio
import logging
from typing import AsyncGenerator, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from app.api.chat_helpers import sse_event, sse_done
from app.core.llm_router import LLMFactory
from app.agents.rag import rag_agent
from app.core.milvus_client import milvus_manager
from app.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["handle_unclear"]


async def handle_unclear(
    safe_query: str,
    user_role: str,
    doc_ids: Optional[list[str]] = None,
    langfuse_callbacks: Optional[list] = None,
) -> AsyncGenerator[str, None]:
    """处理意图不明的查询：轻量检索 → 有结果则 LLM 回答，否则给引导建议"""
    import time
    settings = get_settings()
    _t2 = time.perf_counter()

    yield sse_event("route_fallback", message="意图不明确，正在搜索知识库...")

    try:
        # 轻量检索：只用向量搜索（不触发完整 RAG pipeline；to_thread 隔离同步检索）
        visibility_expr = milvus_manager.build_visibility_expr(user_role, doc_ids)
        quick_results = await asyncio.to_thread(
            rag_agent.rag.search,
            query=safe_query,
            top_k=3,
            visibility_expr=visibility_expr,
        )
        found_chunks = quick_results.get("results", [])

        if found_chunks:
            # 搜到了 → 构建上下文让 LLM 回答
            context_str, sources = rag_agent._format_context(found_chunks, all_chunks=found_chunks)
            confidence = found_chunks[0].get("rerank_score", 0.0)

            yield sse_event("sources", sources=sources, confidence=confidence)

            messages = [
                SystemMessage(content=(
                    "你是一个供应链智能助手。用户的问题意图不明确，但知识库中有相关内容。"
                    "请根据以下上下文，友好地回答用户的问题。如果不是用户想要的，请引导用户明确需求。"
                )),
                HumanMessage(content=(
                    f"用户问题：{safe_query}\n\n"
                    f"知识库相关内容：\n{context_str}\n\n"
                    "请根据以上内容回答。如果与用户问题无关，请友好地引导用户换个方式提问。"
                )),
            ]
            llm = LLMFactory.get_llm(temperature=0.3)
            full_content = ""
            async for chunk in LLMFactory.astream(messages, callbacks=langfuse_callbacks):
                content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                full_content += content
                yield sse_event("content", content=content)
        else:
            # 没搜到 → 坦诚告知 + 给建议
            yield sse_event("content", content=(
                "抱歉，我不确定你具体想问什么。你可以试试：\n"
                "• 查库存：「MAT-001 的库存是多少」\n"
                "• 查制度：「新供应商准入需要什么资质」\n"
                "• 查订单：「PO-20250601 的状态」\n"
                "• 上传图片辅助说明"
            ))
    except Exception as e:
        logger.error(f"UNCLEAR 兜底检索失败: {e}")
        yield sse_event("content", content="抱歉，系统暂时无法处理你的问题，请稍后重试。")

    _t_gen = time.perf_counter() - _t2
    logger.info(f"[UNCLEAR兜底] 耗时={_t_gen*1000:.0f}ms")
    yield sse_done()
