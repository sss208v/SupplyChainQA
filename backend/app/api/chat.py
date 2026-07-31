"""
SupplyChainRAG - 对话API路由（精简路由层）
============================================================
1. SSE (Server-Sent Events) 是AI对话的标准传输协议
2. 意图处理逻辑已拆分到 handlers/ 目录下
3. 本文件仅负责路由分发和公共前置逻辑
"""
import hashlib
import json
import logging
import uuid
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi import Request
from pydantic import BaseModel, Field
from app.agents.router import IntentType
from app.agents.rag import RAGAgent
from app.agents.router import RouterAgent
from app.agents.orchestrator import Orchestrator
from app.core.graph_engine import GraphEngine
from app.core.milvus_client import MilvusManager
from app.core.neo4j_client import Neo4jClient
from app.core.redis_client import RedisManager, ChatMemory
from app.core.query_analyzer import QueryComplexityAnalyzer
from app.core.data_filter import PIIFilter
from app.config import get_settings
from app.core.auth import get_current_user_optional, get_current_user_full
from app.core.dependencies import (
    get_rag_agent, get_router_agent, get_orchestrator_service,
    get_graph_engine, get_milvus_manager, get_neo4j_client,
    get_redis_manager, get_chat_memory,
    get_query_analyzer,
)
from app.api.chat_helpers import sse_event, sse_done, ChatRequest, _AskRequest
from app.api.handlers import (
    handle_greeting, handle_unclear, handle_rag_answer,
    handle_tool_call, handle_goal, handle_hybrid,
    handle_graph_query, handle_ask,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])
_pii_filter = PIIFilter()


# ============================================================
# 模型管理
# ============================================================

@router.get("/model/list")
async def list_models():
    """获取可用模型列表"""
    settings = get_settings()
    return {
        "models": [
            {"provider": "local", "name": settings.LOCAL_LLM_MODEL, "configured": True},
            {"provider": "deepseek", "name": settings.DEEPSEEK_MODEL, "configured": bool(settings.DEEPSEEK_API_KEY)},
            {"provider": "minimax", "name": settings.MINIMAX_MODEL, "configured": bool(settings.MINIMAX_API_KEY)},
            {"provider": "ollama", "name": settings.OLLAMA_MODEL, "configured": True},
        ],
        "current": settings.LLM_PROVIDER,
    }


@router.post("/model/switch")
async def switch_model(body: dict):
    """切换模型"""
    provider = body.get("provider", "")
    if provider not in ("deepseek", "minimax", "ollama", "local"):
        raise HTTPException(status_code=400, detail="不支持的provider")
    return {"message": f"已切换到 {provider}", "provider": provider}


# ============================================================
# 缓存指标（多层缓存命中率监控，仅 admin）
# ============================================================

@router.get("/cache/stats")
async def cache_stats(request: Request):
    """返回 L1/L2/L3 各层缓存命中率指标（L4 由 nginx 承担，不在应用层统计）"""
    from app.core.auth import check_role
    from app.models.user import UserRole
    from app.core.dependencies import get_cache_manager

    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value])

    return {"layers": get_cache_manager().stats()}


# ============================================================
# 非流式 RAG 问答（供评估脚本 / 外部系统集成）
# ============================================================

@router.post("/ask")
async def ask_non_streaming(request: Request, body: _AskRequest):
    """非流式 RAG 问答端点 — 直接走 rag_agent.answer()，跳过意图路由"""
    if get_settings().REQUIRE_AUTH_CHAT:
        from app.core.auth import get_current_user_required
        await get_current_user_required(request)
    result = await handle_ask(body.question, body.doc_ids, request)
    return result


# ============================================================
# 流式对话（SSE）
# ============================================================

@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    rag_agent: RAGAgent = Depends(get_rag_agent),
    router: RouterAgent = Depends(get_router_agent),
    orchestrator: Orchestrator = Depends(get_orchestrator_service),
    graph_engine: GraphEngine = Depends(get_graph_engine),
    milvus: MilvusManager = Depends(get_milvus_manager),
    neo4j: Neo4jClient = Depends(get_neo4j_client),
    redis: RedisManager = Depends(get_redis_manager),
    memory: ChatMemory = Depends(get_chat_memory),
    query_analyzer: QueryComplexityAnalyzer = Depends(get_query_analyzer),
):
    """
    对话接口（流式SSE）

    1. 返回StreamingResponse，content_type="text/event-stream"
    2. 公共前置逻辑：DEMO_MODE → Trace → Session → Query Cache → 意图路由
    3. 按 intent 分发给对应的 handler
    """
    import time
    _t0 = time.perf_counter()
    _t_route = 0.0

    session_id = body.session_id or str(uuid.uuid4())
    safe_query = _pii_filter.filter_text(body.query)
    logger.info(f"对话开始 session={session_id} query={safe_query}")

    # 用户角色：REQUIRE_AUTH_CHAT=True 时强制登录（未登录 401），
    # 关闭时（演示环境）允许匿名并回退到默认角色
    _settings = get_settings()
    _user_role = _settings.DEFAULT_USER_ROLE
    _user_id = ""
    if _settings.REQUIRE_AUTH_CHAT:
        _current_user = await get_current_user_full(request)  # 未登录直接 401
        _user_role = _current_user.get("role", _settings.DEFAULT_USER_ROLE)
        _user_id = _current_user.get("username", "") or _current_user.get("sub", "")
    else:
        try:
            _current_user = await get_current_user_full(request)
            if _current_user:
                _user_role = _current_user.get("role", _settings.DEFAULT_USER_ROLE)
                _user_id = _current_user.get("username", "") or _current_user.get("sub", "")
        except Exception as e:
            logger.warning(f"[Permission] 匿名会话（REQUIRE_AUTH_CHAT=false），使用默认角色: {e}")

    async def event_generator():
        """SSE事件生成器 — 公共前置 + 意图分发"""
        nonlocal _t_route

        settings = get_settings()

        # ---- DEMO_MODE 检查 ----
        if settings.DEMO_MODE:
            yield sse_event("demo_mode", mode="demo", message="当前为离线演示模式，LLM 推理结果由本地降级链路生成")

        try:
            # ---- Trace ID + Langfuse 可观测性 ----
            from app.core.observability import get_trace_id, get_langfuse_url, is_enabled, get_langfuse_callback
            trace_id = get_trace_id()
            langfuse_handler = get_langfuse_callback(trace_id=trace_id)
            langfuse_callbacks = [langfuse_handler] if langfuse_handler else None
            if is_enabled():
                yield sse_event("trace", trace_id=trace_id, langfuse_url=get_langfuse_url(trace_id))

            # ---- 会话ID ----
            yield sse_event("session", session_id=session_id, trace_id=trace_id)

            # ---- Query Cache 检查 ----
            _cache_input = f"{safe_query}:{_user_role}"
            cache_key = f"query_cache:{hashlib.md5(_cache_input.encode()).hexdigest()}"
            try:
                cached = await redis.client.get(cache_key)
                if cached:
                    cached_data = json.loads(cached)
                    yield sse_event("cache_hit", query=safe_query, message="⚡ 缓存命中（MD5匹配，零token重放）")
                    yield sse_event("content", content=cached_data["answer"])
                    if cached_data.get("sources"):
                        yield sse_event("sources", sources=cached_data["sources"], confidence=cached_data.get("confidence", 0))
                    if cached_data.get("token_usage"):
                        yield sse_event("token_usage", usage=cached_data["token_usage"])
                    yield sse_done()
                    logger.info(f"[QueryCache] 缓存命中: {safe_query}, key={cache_key}")
                    return
            except Exception:
                logger.warning("[QueryCache] 缓存读取失败（Redis未连接或异常）")

            # ---- 意图路由 ----
            _t1 = time.perf_counter()
            route_result = await router.route(safe_query)
            intent = route_result["intent"]
            _t_route = time.perf_counter() - _t1
            logger.info(f"[意图路由] intent={intent.value} method={route_result['method']} 耗时={_t_route*1000:.0f}ms")

            yield sse_event(
                "route",
                intent=intent.value,
                confidence=route_result.get("confidence", 0.0),
                method=route_result["method"],
                duration_ms=int(_t_route * 1000),
            )

            # ---- 按意图分发 ----
            if intent == IntentType.GREETING:
                async for event in handle_greeting(safe_query):
                    yield event

            elif intent == IntentType.UNCLEAR:
                async for event in handle_unclear(safe_query, _user_role, body.doc_ids, langfuse_callbacks):
                    yield event

            elif intent == IntentType.RAG_ANSWER:
                async for event in handle_rag_answer(
                    safe_query, _user_role, session_id, body,
                    langfuse_callbacks, _t0, _t_route, _user_id,
                    rag_agent, milvus, neo4j, memory, query_analyzer,
                ):
                    yield event

            elif intent == IntentType.TOOL_CALL:
                tool_name = route_result.get("tool_name", "unknown")
                _needs_clarify = route_result.get("_needs_clarify", False)
                async for event in handle_tool_call(
                    safe_query, tool_name, session_id, _user_id,
                    body.agent_type, body, langfuse_callbacks,
                    redis, _user_role, _needs_clarify,
                ):
                    yield event

            elif intent == IntentType.GOAL:
                async for event in handle_goal(safe_query, session_id, _user_id, orchestrator):
                    yield event

            elif intent == IntentType.HYBRID:
                tool_name = route_result.get("tool_name")
                async for event in handle_hybrid(safe_query, tool_name, _user_role, _user_id, body, rag_agent, milvus):
                    yield event

            elif intent == IntentType.GRAPH_QUERY:
                async for event in handle_graph_query(safe_query, session_id, _user_id, graph_engine, memory):
                    yield event

            else:
                yield sse_event("content", content="抱歉，我不太理解您的问题，请提供更多细节。")

            # ---- 完成信号 ----
            yield sse_done()
            _t_total = time.perf_counter() - _t0
            logger.info(f"对话完成 session={session_id} 总耗时={_t_total*1000:.0f}ms (路由{_t_route*1000:.0f}ms)")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"SSE流式输出错误: {e}\n{tb}")
            yield sse_event("error", message="处理请求时出现异常，请稍后重试。")
            yield sse_done()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# Text-to-SQL 端点
# ============================================================

class SQLQueryRequest(BaseModel):
    """Text-to-SQL 查询请求"""
    question: str = Field(..., min_length=1, max_length=1000, description="自然语言问题")
    user_role: str = Field(
        "employee",
        description="用户角色（白名单内：admin/finance/sales/developer/employee/manager）",
    )


_ALLOWED_SQL_ROLES = frozenset({
    "admin", "finance", "sales", "developer", "employee", "manager", "public",
})


@router.post("/sql")
async def sql_query(request: Request, body: SQLQueryRequest):
    """Text-to-SQL：自然语言转 SQL 查询（只允许 SELECT，自动 LIMIT 100）"""
    from fastapi import HTTPException
    from app.core.text_to_sql import get_text_to_sql

    auth_header = request.headers.get("Authorization", "")
    has_token = bool(auth_header and auth_header.startswith("Bearer "))

    if has_token:
        try:
            current_user = await get_current_user_optional(request)
        except Exception as e:
            logger.warning(f"[Auth] /sql token 解析失败: {e}")
            raise HTTPException(status_code=401, detail="无效的认证令牌")
        if current_user is None:
            raise HTTPException(status_code=401, detail="认证令牌无效或已过期")
        user_role = current_user.get("role", "employee")
    else:
        if body.user_role and body.user_role not in _ALLOWED_SQL_ROLES:
            raise HTTPException(status_code=403, detail=f"未授权的角色：{body.user_role}")
        if body.user_role and body.user_role != "employee":
            logger.warning(f"[Auth] /sql 匿名用户尝试 role={body.user_role}，已被强制降级为 employee")
        user_role = "employee"

    engine = get_text_to_sql()
    result = await engine.execute(body.question, user_role)

    return {
        "question": body.question,
        "sql": result.sql,
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "error": result.error,
        "execution_ms": result.execution_ms,
        "formatted": engine.format_result(result),
    }
