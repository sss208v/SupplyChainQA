"""
HYBRID intent handler — 知识库 + 业务系统双重检索

先进行 RAG 检索，再调用工具查询业务系统，
最后融合两部分结果由 LLM 生成统一回答。
"""
import asyncio
import logging
from typing import AsyncGenerator, Optional
from app.api.chat_helpers import sse_event, ChatRequest
from app.api.tool import _is_tool_allowed
from app.agents.rag import RAGAgent
from app.core.llm_router import LLMFactory
from app.config import get_settings
from app.core.milvus_client import MilvusManager
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

__all__ = ["handle_hybrid"]


async def handle_hybrid(
    safe_query: str,
    tool_name: Optional[str],
    user_role: str,
    user_id: str,
    body: ChatRequest,
    rag_agent: RAGAgent,
    milvus: MilvusManager,
) -> AsyncGenerator[str, None]:
    """处理混合检索意图

    步骤：
    1. RAG 检索知识库
    2. 调用业务系统工具
    3. 融合两部分结果生成回答
    """
    settings = get_settings()

    yield sse_event("hybrid_start", message="正在检索知识库并调用业务系统...")

    # 1. RAG 检索（to_thread 隔离同步检索，避免阻塞事件循环）
    _vis_expr = milvus.build_visibility_expr(user_role, body.doc_ids)
    rag_result = await asyncio.to_thread(
        rag_agent.rag.search, safe_query, top_k=settings.RERANK_TOP_K, visibility_expr=_vis_expr
    )
    results = rag_result.get("results", [])
    context_str = ""
    sources = []
    if results:
        context_str, sources = rag_agent._format_context(results, all_chunks=results)
        confidence = results[0].get("rerank_score", 0.0)
        yield sse_event("sources", sources=sources, confidence=confidence)

    # 2. 工具调用
    tool_result_str = ""
    if tool_name and _is_tool_allowed(tool_name, user_role):
        yield sse_event("tool_status", status="calling", tool=tool_name)
        try:
            from app.agents.tool import tool_agent
            tool_out = await tool_agent.execute(tool_name, query=safe_query, user_id=user_id)
            tool_result_str = tool_out.get("result", tool_out.get("observation", ""))
            yield sse_event("tool_call", tool=tool_name, input=safe_query, observation=tool_result_str)
        except Exception as _te:
            logger.warning(f"[Hybrid] 工具 {tool_name} 失败: {_te}")
            yield sse_event("error", message=f"工具 {tool_name} 调用失败")

    # 3. 融合生成
    parts = []
    if context_str:
        parts.append(f"【知识库资料】\n{context_str}")
    if tool_result_str:
        parts.append(f"【业务系统查询结果】\n{tool_result_str}")

    if not parts:
        logger.warning("[Hybrid] RAG和工具均无结果，回退到RAG_ANSWER")
        await asyncio.to_thread(rag_agent.rag.search, safe_query, top_k=3, visibility_expr=_vis_expr)
        yield sse_event("content", content="抱歉，知识库和业务系统当前均未查到相关信息。请换个方式描述您的问题。")
    else:
        try:
            fusion_prompt = f"""你是一个供应链智能助手。用户需要同时查询知识库和业务系统数据。
请融合以下两部分信息回答。如果某部分为空，忽略即可。

{chr(10).join(parts)}

用户问题：{safe_query}

请结合以上信息全面回答，并标注信息来源（知识库/业务系统）。"""
            _llm = LLMFactory.get_llm(temperature=0, streaming=False)
            _response = await _llm.ainvoke([SystemMessage(content=fusion_prompt)])
            _answer = _response.content.strip()
            yield sse_event("content", content=_answer)
        except Exception as _fusion_e:
            logger.error(f"[Hybrid] 融合生成失败: {_fusion_e}")
            yield sse_event("content", content="抱歉，系统处理您的查询时出现异常，请稍后重试。")

    logger.info(f"[HYBRID] 工具={tool_name} RAG={len(results)}")
