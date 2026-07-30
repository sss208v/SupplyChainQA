"""
GRAPH_QUERY intent handler — 供应链知识图谱查询

查询 Neo4j 供应链实体关系图谱，返回结构化数据并由 LLM 生成自然语言回答。
"""
import logging
from typing import AsyncGenerator, Optional
from app.api.chat_helpers import sse_event, sse_done
from app.core.graph_engine import GraphEngine
from app.core.llm_router import LLMFactory
from app.core.redis_client import ChatMemory
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

__all__ = ["handle_graph_query"]


async def handle_graph_query(
    safe_query: str,
    session_id: str,
    user_id: str,
    graph_engine: GraphEngine,
    memory: Optional[ChatMemory],
) -> AsyncGenerator[str, None]:
    """处理图谱查询意图

    查询供应链实体关系图谱，将结构化结果经 LLM 转换为自然语言回答。
    """
    yield sse_event("graph_query_start", message="正在查询供应链实体关系图谱...")

    try:
        graph_result = await graph_engine.query(safe_query)
    except Exception as e:
        logger.error(f"图谱查询异常: {e}")
        yield sse_event("error", message=f"图谱查询失败: {e}")
        return

    # 优先检查 error 字段
    if graph_result.get("error"):
        yield sse_event("content", content=f"图谱查询失败：{graph_result['error']}")
        return

    if graph_result.get("rows"):
        yield sse_event(
            "graph_result",
            pattern=graph_result.get("pattern"),
            entities=graph_result.get("entities"),
            row_count=len(graph_result["rows"]),
        )

        graph_context = graph_engine.format_results(graph_result)
        try:
            llm = LLMFactory.get_llm(temperature=0.3)
            prompt = f"以下是一段供应链实体关系图谱的查询结果：\n\n{graph_context}\n\n请根据这些结构化数据，用中文回答用户的原始问题：{safe_query}\n\n要求：简洁、直接、有数据支撑。"
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            full_answer = response.content.strip()
            yield sse_event("content", content=full_answer)
            if session_id and memory:
                try:
                    await memory.add_message(session_id, "user", safe_query, user_id=user_id)
                    await memory.add_message(session_id, "assistant", full_answer, user_id=user_id)
                except Exception as e:
                    logger.debug(f"[Memory] 对话记忆保存失败: {e}")
        except Exception as e:
            logger.error(f"图谱 LLM 生成失败: {e}")
            yield sse_event("content", content=f"图谱查询结果如下：\n\n{graph_context}")
    else:
        yield sse_event(
            "content",
            content="未在供应链图谱中找到相关实体关系，请确认物料/订单/供应商编码是否正确。",
        )

    logger.info(f"[GRAPH检索处理] pattern={graph_result.get('pattern')} rows={len(graph_result.get('rows',[]))}")
