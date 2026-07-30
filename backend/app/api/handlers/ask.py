"""
ASK handler — 非流式 RAG 问答（供评估脚本/外部系统集成）

直接走 RAG pipeline（跳过意图路由），返回完整回答和来源。
"""
import logging
import time
from app.api.chat_helpers import _build_rag_demo_answer
from app.agents.rag import rag_agent
from app.core.data_filter import PIIFilter
from app.config import get_settings
from app.core.auth import get_current_user_optional

logger = logging.getLogger(__name__)

_pii_filter = PIIFilter()

__all__ = ["handle_ask"]


async def handle_ask(question: str, doc_ids: list[str] = None, request=None) -> dict:
    """非流式 RAG 问答

    Args:
        question: 用户问题
        doc_ids: 限定检索的文档ID列表
        request: FastAPI Request（可选，用于获取用户身份）

    Returns:
        dict: 回答结果
    """
    t0 = time.time()

    # PII 脱敏
    safe_question = _pii_filter.filter_text(question)

    # 用户身份（可选，支持匿名评估）
    if request:
        try:
            await get_current_user_optional(request)
        except Exception as e:
            logger.warning(f"[Auth] 获取用户信息失败: {e}")

    # 直接调用 RAG Agent（跳过意图路由）
    try:
        result = await rag_agent.answer(
            query=safe_question,
            session_id=None,  # 评估不需要对话记忆
            doc_ids=doc_ids,
        )
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"[Ask] RAG pipeline 异常: {type(e).__name__}: {e}")
        error_detail = str(e)
        if "502" in error_detail or "503" in error_detail or "Connection" in error_detail:
            return {
                "answer": "",
                "sources": [],
                "confidence": 0.0,
                "query_type": "",
                "context_used": 0,
                "elapsed_seconds": round(elapsed, 1),
                "error": "LLM 服务不可用，请确认 llama.cpp 已启动 (localhost:18080)",
            }
        return {
            "answer": "",
            "sources": [],
            "confidence": 0.0,
            "query_type": "",
            "context_used": 0,
            "elapsed_seconds": round(elapsed, 1),
            "error": f"处理请求时出错: {type(e).__name__}",
        }

    elapsed = time.time() - t0
    logger.info(f"[Ask] {safe_question[:40]}... ({elapsed:.1f}s, confidence={result.get('confidence', 0):.2f})")

    return {
        "answer": result["answer"],
        "sources": result.get("sources", []),
        "confidence": result.get("confidence", 0.0),
        "query_type": result.get("query_type", ""),
        "context_used": result.get("context_used", 0),
        "elapsed_seconds": round(elapsed, 1),
    }
