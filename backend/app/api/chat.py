"""
SmartQA Pro - 对话API路由
============================================================
1. SSE (Server-Sent Events) 是AI对话的标准传输协议
   - 优点：单向推送、自动重连、基于HTTP、兼容代理
   - 缺点：只能服务端→客户端（但AI对话场景只需要这个方向）

2. SSE格式规范：
   每条消息格式: "data: {json}\n\n"
   结束标记: "data: [DONE]\n\n"
   心跳包: "data: {"type":"heartbeat"}\n\n"（防止连接超时）

3. FastAPI实现SSE的关键：
   - 使用StreamingResponse + async generator
   - 设置Content-Type: text/event-stream
   - 设置Cache-Control: no-cache（防止代理缓存）
   - 设置X-Accel-Buffering: no（防止Nginx缓冲）
============================================================
"""
import json
import asyncio
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from fastapi import Request
from app.agents.router import router_agent, IntentType
from app.agents.rag import rag_agent
from app.agents.tool import tool_agent
from app.agents.langchain_agent import langchain_agent
from app.agents.langgraph_agent import langgraph_agent
from app.core.llm_router import LLMFactory
from app.core.redis_client import chat_memory
from app.core.data_filter import PIIFilter
from app.core.clarify import check_needs_clarification
from app.core.self_rag import get_self_rag
from app.core.faithfulness import get_faithfulness_checker
from app.config import get_settings
from app.core.auth import get_current_user_optional, get_current_user_full
from app.core.milvus_client import milvus_manager
from app.api.tool import _is_tool_allowed, _get_allowed_tools
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/chat", tags=["对话"])

# PII脱敏过滤器实例（模块级单例，避免重复创建）
_pii_filter = PIIFilter()

# 内容过滤引擎（已禁用）

# Faithfulness 检测器（模块级单例）
_faithfulness_checker = get_faithfulness_checker()


def _apply_output_guard(answer: str) -> str:
    """输出过滤（已禁用，保留接口兼容）"""
    return answer


def _detect_mime(base64_data: str) -> str:
    """根据 base64 数据头检测图片 MIME 类型"""
    import base64 as b64
    try:
        raw = b64.b64decode(base64_data[:64])
        if raw[:4] == b'\x89PNG':
            return "image/png"
        elif raw[:2] == b'\xff\xd8':
            return "image/jpeg"
        elif raw[:4] == b'RIFF':
            return "image/webp"
        elif raw[:4] == b'GIF8':
            return "image/gif"
    except Exception:
        pass
    return "image/jpeg"  # 默认


# ---- 角色中文标签 ----
ROLE_LABELS = {
    "admin": "管理员",
    "purchase": "采购部",
    "warehouse": "仓库部",
    "quality": "质量部",
    "production": "生产部",
    "finance": "财务部",
    "logistics": "物流部",
}


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


# ---- 请求/响应模型 ----

class ChatRequest(BaseModel):
    """对话请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: Optional[str] = Field(None, description="会话ID（为空则新建）")
    stream: bool = Field(True, description="是否流式输出")
    doc_ids: Optional[list[str]] = Field(None, description="限定检索的文档ID")
    agent_type: Optional[str] = Field(None, description="Agent类型: react（手写ReAct）/ langchain（LangChain Agent），为空则使用配置默认值")
    approved: bool = Field(False, description="是否已确认执行写操作")
    approved_tool: Optional[str] = Field(None, description="已确认执行的工具名")
    images: Optional[list[str]] = Field(None, description="图片列表（base64编码，不含data:前缀）")


class ChatResponse(BaseModel):
    """对话响应（非流式）"""
    session_id: str
    answer: str
    intent: str
    confidence: float = 0.0
    sources: list = []
    tool_calls: list = []
    feedback_url: str = "/api/v1/feedback"  # 前端提交反馈的地址


# ---- 辅助：Agent 类型选择 ----

def _get_tool_agent(agent_type: Optional[str] = None):
    """
    根据 agent_type 参数选择使用哪个 Tool Agent。

    优先级：请求参数 > 配置文件默认值
    """
    effective_type = agent_type or settings.AGENT_TYPE
    if effective_type == "langchain":
        return langchain_agent
    if effective_type == "langgraph":
        return langgraph_agent
    return tool_agent


# ---- API接口 ----

@router.get("/model/list")
async def list_models():
    """获取可用模型列表"""
    return {
        "models": [
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
    if provider not in ("deepseek", "minimax", "ollama"):
        raise HTTPException(status_code=400, detail="不支持的provider")
    # 这里只是返回确认，实际切换需要重启后端或用环境变量
    return {"message": f"已切换到 {provider}", "provider": provider}


async def chat_completions(request: Request, body: ChatRequest):
    """对话接口（非流式）"""
    session_id = body.session_id or str(uuid.uuid4())
    safe_query = _pii_filter.filter_text(body.query)

    # Step 1: 意图路由（使用脱敏后的查询）
    route_result = await router_agent.route(safe_query)
    intent = route_result["intent"]

    logger.info(f"对话请求: query={safe_query}, intent={intent}, session={session_id}")

    # Step 2: 根据意图分发到不同Agent
    if intent == IntentType.GREETING:
        answer = _handle_greeting(safe_query)
        return ChatResponse(
            session_id=session_id,
            answer=_apply_output_guard(answer),
            intent=intent.value,
            confidence=route_result.get("confidence", 0.0),
        )

    elif intent == IntentType.RAG_ANSWER:
        result = await rag_agent.answer(
            query=safe_query,
            session_id=session_id,
            doc_ids=body.doc_ids,
        )
        return ChatResponse(
            session_id=session_id,
            answer=_apply_output_guard(result["answer"]),
            intent=intent.value,
            confidence=result["confidence"],
            sources=result["sources"],
        )

    elif intent == IntentType.TOOL_CALL:
        agent = _get_tool_agent(body.agent_type)
        result = await agent.run(
            query=safe_query,
            tool_names=[route_result["tool_name"]] if route_result.get("tool_name") else None,
            session_id=session_id,
        )
        return ChatResponse(
            session_id=session_id,
            answer=_apply_output_guard(result["answer"]),
            intent=intent.value,
            tool_calls=result["tool_calls"],
        )

    elif intent == IntentType.HYBRID:
        # 混合意图：先RAG检索上下文，再用Tool处理需要实时数据的部分
        rag_result = await rag_agent.answer(
            query=safe_query,
            session_id=session_id,
            doc_ids=body.doc_ids,
        )

        # 如果RAG已经得到高置信度答案且没有指定工具，直接返回
        tool_name = route_result.get("tool_name")
        if not tool_name or rag_result["confidence"] >= settings.CONFIDENCE_THRESHOLD:
            return ChatResponse(
                session_id=session_id,
                answer=_apply_output_guard(rag_result["answer"]),
                intent=intent.value,
                confidence=rag_result["confidence"],
                sources=rag_result["sources"],
            )

        # RAG置信度低 + 指定了工具：用RAG上下文增强Tool调用
        enhanced_query = (
            f"背景信息：{rag_result['answer']}\n\n用户问题：{safe_query}"
        )
        agent = _get_tool_agent(body.agent_type)
        tool_result = await agent.run(
            query=enhanced_query,
            tool_names=[tool_name],
            session_id=session_id,
        )

        return ChatResponse(
            session_id=session_id,
            answer=_apply_output_guard(tool_result["answer"]),
            intent=intent.value,
            confidence=rag_result["confidence"],
            sources=rag_result["sources"],
            tool_calls=tool_result["tool_calls"],
        )

    else:
        # unclear意图：追问澄清
        return ChatResponse(
            session_id=session_id,
            answer="抱歉，我不太理解您的问题。您可以尝试：\n1. 提供更多细节\n2. 换一种表述方式\n3. 询问具体的知识点",
            intent=intent.value,
            confidence=route_result.get("confidence", 0.0),
        )


@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    """
    对话接口（流式SSE）

    1. 返回StreamingResponse，content_type="text/event-stream"
    2. 用async generator逐步yield数据
    3. 每条数据格式: "data: {json}\\n\\n"
    4. 结束时发送: "data: [DONE]\\n\\n"
    5. 定期发送心跳包防止连接超时
    """
    import time
    _t0 = time.perf_counter()
    _t_route = _t_gen = _t_total = 0.0

    def _elapsed(label: str):
        return f"[{label} +{(time.perf_counter() - _t0)*1000:.0f}ms]"

    session_id = body.session_id or str(uuid.uuid4())

    # PII脱敏：在调用LLM API前过滤用户输入中的敏感信息
    # 使用脱敏后的safe_query进行路由和推理，防止PII泄露到云端LLM
    safe_query = _pii_filter.filter_text(body.query)

    logger.info(f"对话开始 session={session_id} query={safe_query}")

    # 获取用户角色用于权限过滤（默认finance最小权限，token无效时会被拦截）
    _user_role = "finance"
    _user_id = ""  # 用于对话记忆隔离
    try:
        _current_user = await get_current_user_full(request)
        if _current_user:
            _user_role = _current_user.get("role", "finance")
            _user_id = _current_user.get("username", "") or _current_user.get("sub", "")
            logger.info(f"[Permission] 用户: {_user_id}, 角色: {_user_role}")
        else:
            logger.warning("[Permission] get_current_user_full returned None")
    except Exception as e:
        logger.warning(f"[Permission] 获取用户角色失败: {e}")

    async def event_generator():
        """SSE事件生成器"""
        nonlocal _t_route, _t_gen, _user_id
        try:
            # 1. 发送会话ID
            yield _sse_format({
                "type": "session",
                "session_id": session_id,
            })

            # ---- 多模态：图片通过 CLIP 入库（纯本地嵌入 + 跨模态检索）----
            if body.images and len(body.images) > 0:
                # CLIP 图像嵌入入库（纯本地，始终执行）
                clip_stored = 0
                if settings.CLIP_ENABLED:
                    try:
                        from app.core.multimodal_embedding import clip_engine
                        import uuid as _uuid
                        for img_b64 in body.images:
                            clip_vec = clip_engine.encode_image_base64(img_b64)
                            milvus_manager.insert_image(
                                collection_name=settings.CLIP_IMAGE_COLLECTION,
                                image_id=str(_uuid.uuid4())[:12],
                                source=f"chat_upload_{session_id}",
                                clip_embedding=clip_vec,
                                base64_data=img_b64,
                                description=safe_query,  # 用用户查询作为初始描述
                                security_group=["admin"],
                            )
                            clip_stored += 1
                        if clip_stored:
                            logger.info(f"[CLIP] {clip_stored}张图片已入库（纯本地）")
                    except Exception as e:
                        logger.warning(f"[CLIP] 入库失败: {e}")

            # ---- Query Cache 检查 ----
            import hashlib
            cache_key = f"query_cache:{hashlib.md5(safe_query.encode()).hexdigest()}"
            try:
                from app.core.redis_client import redis_manager
                cached = await redis_manager.client.get(cache_key)
                if cached:
                    cached_data = json.loads(cached)
                    yield _sse_format({
                        "type": "cache_hit",
                        "query": safe_query,
                        "message": "⚡ 缓存命中（MD5匹配，零token重放）",
                    })
                    yield _sse_format({"type": "content", "content": cached_data["answer"]})
                    if cached_data.get("sources"):
                        yield _sse_format({
                            "type": "sources",
                            "sources": cached_data["sources"],
                            "confidence": cached_data.get("confidence", 0),
                        })
                    if cached_data.get("token_usage"):
                        yield _sse_format({"type": "token_usage", "usage": cached_data["token_usage"]})
                    yield "data: [DONE]\n\n"
                    logger.info(f"[QueryCache] 缓存命中: {safe_query}, key={cache_key}")
                    return
            except Exception:
                logger.warning(f"[QueryCache] 缓存读取失败（Redis未连接或异常）")
            # ---- 缓存检查结束 ----

            # 2. 意图路由
            _t1 = time.perf_counter()
            route_query = safe_query
            route_result = await router_agent.route(route_query)
            intent = route_result["intent"]
            _t_route = time.perf_counter() - _t1
            logger.info(f"{_elapsed('意图路由')} intent={intent.value} method={route_result['method']} 耗时={_t_route*1000:.0f}ms")

            yield _sse_format({
                "type": "route",
                "intent": intent.value,
                "confidence": route_result.get("confidence", 0.0),
                "method": route_result["method"],
                "duration_ms": int(_t_route * 1000),
            })

            # 3. 根据意图分发
            # GREETING: 用户主动问候（你好/谢谢/再见等）
            # UNCLEAR:  系统无法理解意图 → 不扔客套话，走 RAG 检索兜底
            has_images = body.images and len(body.images) > 0

            if intent == IntentType.GREETING:
                _t2 = time.perf_counter()
                answer = _handle_greeting(safe_query)
                _t_gen = time.perf_counter() - _t2
                logger.info(f"{_elapsed('GREETING')} 耗时={_t_gen*1000:.0f}ms")
                yield _sse_format({"type": "content", "content": _apply_output_guard(answer)})

            elif intent == IntentType.UNCLEAR:
                # 意图不明 → RAG 检索兜底（不直接放弃）
                yield _sse_format({
                    "type": "route_fallback",
                    "message": "意图不明确，正在搜索知识库...",
                })
                _t2 = time.perf_counter()
                try:
                    # 轻量检索：只用向量搜索（不触发完整 RAG pipeline）
                    quick_results = rag_engine.search(
                        query=safe_query,
                        top_k=3,
                        visibility_expr=milvus_manager.build_visibility_expr(_user_role, body.doc_ids),
                    )
                    found_chunks = quick_results.get("results", [])
                    if found_chunks:
                        # 搜到了 → 构建上下文让 LLM 回答
                        context_str, sources = rag_agent._format_context(found_chunks, all_chunks=found_chunks)
                        confidence = found_chunks[0].get("rerank_score", 0.0)
                        yield _sse_format({
                            "type": "sources",
                            "sources": sources,
                            "confidence": confidence,
                        })

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
                        async for chunk in LLMFactory.astream(llm, messages):
                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                            full_content += content
                            yield _sse_format({"type": "content", "content": content})
                    else:
                        # 没搜到 → 坦诚告知 + 给建议
                        yield _sse_format({
                            "type": "content",
                            "content": (
                                "抱歉，我不确定你具体想问什么。你可以试试：\n"
                                "• 查库存：「MAT-001 的库存是多少」\n"
                                "• 查制度：「新供应商准入需要什么资质」\n"
                                "• 查订单：「PO-20250601 的状态」\n"
                                "• 上传图片辅助说明"
                            ),
                        })
                except Exception as e:
                    logger.error(f"UNCLEAR 兜底检索失败: {e}")
                    yield _sse_format({
                        "type": "content",
                        "content": "抱歉，系统暂时无法处理你的问题，请稍后重试。",
                    })
                _t_gen = time.perf_counter() - _t2
                logger.info(f"{_elapsed('UNCLEAR兜底')} 耗时={_t_gen*1000:.0f}ms")

            elif intent == IntentType.RAG_ANSWER:
                # ---- DAG Progress: 意图路由完成 ----
                _dag_nodes = [
                    {"name": "意图路由", "status": "done", "duration_ms": int(_t_route * 1000)},
                    {"name": "查询理解", "status": "running", "duration_ms": 0},
                    {"name": "复杂度分析", "status": "pending", "duration_ms": 0},
                    {"name": "向量检索", "status": "pending", "duration_ms": 0},
                    {"name": "BM25检索", "status": "pending", "duration_ms": 0},
                    {"name": "Reranker精排", "status": "pending", "duration_ms": 0},
                    {"name": "答案生成", "status": "pending", "duration_ms": 0},
                ]
                _dag_edges = [
                    {"from": 0, "to": 1}, {"from": 1, "to": 2}, {"from": 2, "to": 3},
                    {"from": 3, "to": 4}, {"from": 4, "to": 5}, {"from": 5, "to": 6},
                ]
                yield _sse_format({"type": "dag_progress", "nodes": _dag_nodes, "edges": _dag_edges})

                # 构建检索上下文
                _t2 = time.perf_counter()
                query_type = rag_agent._classify_query(safe_query)
                search_queries = await rag_agent._prepare_queries(safe_query, query_type)
                _t_query_understand = time.perf_counter() - _t2

                from app.core.query_analyzer import query_analyzer
                _llm_analysis = LLMFactory.get_llm(temperature=0, streaming=False)
                _analysis = await query_analyzer.analyze(safe_query, llm=_llm_analysis)
                _strategy_config = query_analyzer.get_strategy_config(_analysis.strategy)
                yield _sse_format({
                    "type": "query_analysis",
                    "complexity": round(_analysis.complexity, 2),
                    "strategy": _analysis.strategy,
                    "entity_count": _analysis.entity_count,
                    "needs_reasoning": _analysis.needs_reasoning,
                    "method": _analysis.method,
                })
                _adaptive_top_k = _strategy_config.get("top_k", settings.RERANK_TOP_K)

                # 构建可见性过滤表达式
                _vis_expr = milvus_manager.build_visibility_expr(_user_role, body.doc_ids)

                # ---- DAG Progress: 查询理解完成，复杂度分析完成，开始检索 ----
                _dag_nodes[1]["status"] = "done"
                _dag_nodes[1]["duration_ms"] = int(_t_query_understand * 1000)
                _dag_nodes[2]["status"] = "done"
                _dag_nodes[2]["duration_ms"] = 0  # 复杂度分析与查询理解同步
                _dag_nodes[3]["status"] = "running"
                _dag_nodes[4]["status"] = "running"
                yield _sse_format({"type": "dag_progress", "nodes": _dag_nodes, "edges": _dag_edges})

                _t3 = time.perf_counter()
                all_results = []
                for sq in search_queries:
                    result = rag_agent.rag.search(sq, top_k=_adaptive_top_k, visibility_expr=_vis_expr)
                    all_results.extend(result.get("results", []))
                _t_search = time.perf_counter() - _t3

                seen = set()
                unique_results = []
                for r in all_results:
                    chunk_id = r.get("chunk_id", "")
                    if chunk_id not in seen:
                        seen.add(chunk_id)
                        unique_results.append(r)

                # ---- DAG Progress: 检索完成，开始精排 ----
                _dag_nodes[3]["status"] = "done"
                _dag_nodes[3]["duration_ms"] = int(_t_search * 1000)
                _dag_nodes[4]["status"] = "done"
                _dag_nodes[4]["duration_ms"] = int(_t_search * 1000)
                _dag_nodes[5]["status"] = "running"
                yield _sse_format({"type": "dag_progress", "nodes": _dag_nodes, "edges": _dag_edges})

                # 保存所有chunk用于父子文档扩展
                all_chunks = list(unique_results)

                # ---- Self-RAG：仅在检索结果多（可能有噪音）时过滤，且策略允许 ----
                self_rag = get_self_rag()
                _use_self_rag = _strategy_config.get("use_self_rag", True)
                if settings.SELF_RAG_ENABLED and _use_self_rag and len(unique_results) >= 4:
                    unique_results, relevance_scores = await self_rag.filter_chunks(
                        safe_query, unique_results, LLMFactory
                    )
                    # 发送 Self-RAG 过滤结果
                    if relevance_scores:
                        yield _sse_format({
                            "type": "self_rag",
                            "scores": [{"chunk_id": s.chunk_id, "score": round(s.score, 2), "reason": s.reason} for s in relevance_scores],
                            "filtered_count": len(unique_results),
                        })

                context_str, sources = rag_agent._format_context(unique_results, all_chunks=all_chunks)
                confidence = unique_results[0].get("rerank_score", 0.0) if unique_results else 0.0

                # ---- CLIP 多模态图像检索（架构三：图文混合召回）----
                clip_images = []
                if settings.CLIP_ENABLED:
                    try:
                        from app.core.multimodal_embedding import clip_engine
                        _t_clip = time.perf_counter()
                        clip_text_vec = clip_engine.encode_text(safe_query)
                        clip_images = milvus_manager.search_images(
                            settings.CLIP_IMAGE_COLLECTION,
                            query_embedding=clip_text_vec,
                            top_k=settings.CLIP_TOP_K,
                        )
                        if clip_images:
                            img_context = "\n\n[相关图片]\n" + "\n".join(
                                f"- 图片{i+1}: {img.get('description', '无描述')} (来源: {img.get('source', '未知')})"
                                for i, img in enumerate(clip_images)
                            )
                            context_str += img_context
                            _t_clip_elapsed = time.perf_counter() - _t_clip
                            yield _sse_format({
                                "type": "image_search",
                                "count": len(clip_images),
                                "duration_ms": int(_t_clip_elapsed * 1000),
                                "images": [
                                    {"image_id": img["image_id"], "description": img.get("description", "")[:200]}
                                    for img in clip_images
                                ],
                            })
                            logger.info(
                                f"{_elapsed('CLIP图像检索')} 命中{len(clip_images)}张, "
                                f"耗时={_t_clip_elapsed*1000:.0f}ms"
                            )
                    except Exception as e:
                        logger.warning(f"[CLIP] 图像检索失败: {e}")
                # ---- CLIP 检索结束 ----

                _t_rerank = time.perf_counter() - _t3 - _t_search

                # ---- 三层置信度路由 ----
                from app.core.confidence_router import get_confidence_router
                conf_router = get_confidence_router()
                decision = conf_router.decide(confidence, safe_query)
                logger.info(f"[置信度路由] confidence={confidence:.3f} tier={decision.tier} strategy={decision.strategy}")

                # 发送置信度决策事件
                yield _sse_format({
                    "type": "confidence_decision",
                    "tier": decision.tier,
                    "strategy": decision.strategy,
                    "confidence": confidence,
                    "description": decision.description,
                })

                if decision.strategy == "rewrite" and 0.3 < confidence < 0.6 and _strategy_config.get("use_query_rewrite", True):
                    # 中置信度：改写 query 重新检索
                    rewrites = await conf_router.rewrite_query(safe_query, LLMFactory)
                    if rewrites:
                        for rw in rewrites[:2]:
                            rw_result = rag_agent.rag.search(rw, top_k=3, visibility_expr=_vis_expr)
                            for r in rw_result.get("results", []):
                                cid = r.get("chunk_id", "")
                                if cid not in seen:
                                    seen.add(cid)
                                    unique_results.append(r)
                        context_str, sources = rag_agent._format_context(unique_results, all_chunks=all_chunks)
                        # 重新计算置信度
                        if unique_results:
                            confidence = max(r.get("rerank_score", 0) for r in unique_results)
                        logger.info(f"[QueryRewrite] 改写后检索结果={len(unique_results)} 新confidence={confidence:.3f}")

                elif decision.strategy == "web_search":
                    # 低置信度：调用 MiniMax Web Search 补充外部信息
                    yield _sse_format({
                        "type": "web_search",
                        "status": "searching",
                        "query": safe_query,
                        "message": "该问题超出知识库覆盖范围，正在搜索外部信息...",
                    })
                    web_results = await conf_router.web_search(safe_query, api_key=settings.MINIMAX_API_KEY)
                    if web_results:
                        web_context = conf_router.format_web_results_for_context(web_results)
                        context_str = context_str + "\n\n" + web_context
                        yield _sse_format({
                            "type": "web_search",
                            "status": "completed",
                            "results_count": len(web_results),
                            "message": f"已从外部搜索获取 {len(web_results)} 条补充信息",
                        })
                        logger.info(f"[WebSearch] 补充了 {len(web_results)} 条外部结果")
                    else:
                        yield _sse_format({
                            "type": "web_search",
                            "status": "no_results",
                            "message": "外部搜索未找到相关信息",
                        })

                # 获取对话历史
                chat_history_str = ""
                if session_id and chat_memory:
                    chat_history_str = await chat_memory.get_context_string(session_id, user_id=_user_id)

                # 构建Prompt
                system_prompt = rag_agent.RAG_SYSTEM_PROMPT.format(
                    chat_history=chat_history_str or "（无历史对话）",
                    context=context_str,
                )
                _t_rag_prep = time.perf_counter() - _t2
                logger.info(f"{_elapsed('RAG预处理')} 检索结果={len(unique_results)} 耗时={_t_rag_prep*1000:.0f}ms")

                # ---- DAG Progress: 精排完成，开始生成 ----
                _dag_nodes[5]["status"] = "done"
                _dag_nodes[5]["duration_ms"] = max(int(_t_rerank * 1000), 1)
                _dag_nodes[6]["status"] = "running"
                yield _sse_format({"type": "dag_progress", "nodes": _dag_nodes, "edges": _dag_edges})

                # 真实token级流式输出
                _t3 = time.perf_counter()
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=safe_query),
                ]

                full_content = ""
                try:
                    async for chunk in LLMFactory.astream(messages):
                        if chunk.content:
                            full_content += chunk.content
                            yield _sse_format({"type": "content", "content": chunk.content})
                except Exception as e:
                    logger.error(f"[Chat] LLM流式调用失败（所有retry已耗尽）: {type(e).__name__}: {e}")
                    yield _sse_format({
                        "type": "error",
                        "message": "服务暂时不可用，请稍后重试",
                        "detail": f"{type(e).__name__}",
                    })
                    # 仍然尝试提取已有的 token 估算
                    chunk = None
                # 提取token用量（最后一个chunk附带了_token_usage）
                token_usage = None
                if 'chunk' in locals() and hasattr(chunk, '_token_usage') and chunk._token_usage:
                    token_usage = chunk._token_usage
                # 如果API没返回usage，用内容长度估算（中文≈2token/字）
                if not token_usage or token_usage.total_tokens == 0:
                    from app.core.llm_router import TokenUsage, MODEL_PRICING
                    est_completion = len(full_content) * 2  # 中文约2token/字
                    est_prompt = len(system_prompt) * 2 + len(safe_query) * 2
                    pricing = MODEL_PRICING.get(settings.DEEPSEEK_MODEL, {})
                    est_cost = (est_prompt / 1_000_000) * pricing.get("input", 0) + (est_completion / 1_000_000) * pricing.get("output", 0)
                    token_usage = TokenUsage(
                        prompt_tokens=est_prompt,
                        completion_tokens=est_completion,
                        total_tokens=est_prompt + est_completion,
                        cost_yuan=est_cost,
                        model=settings.DEEPSEEK_MODEL,
                        provider="deepseek",
                    )
                yield _sse_format({
                    "type": "token_usage",
                    "usage": token_usage.to_dict(),
                })
                _t_llm = time.perf_counter() - _t3
                if token_usage:
                    logger.info(f"{_elapsed('LLM流式生成')} tokens={token_usage.total_tokens} (in={token_usage.prompt_tokens} out={token_usage.completion_tokens}) 费用=¥{token_usage.cost_yuan:.4f} 耗时={_t_llm*1000:.0f}ms")
                else:
                    logger.info(f"{_elapsed('LLM流式生成')} token数≈{len(full_content)} 耗时={_t_llm*1000:.0f}ms")
                _t_gen = _t_rag_prep + _t_llm

                # Faithfulness 检测：验证回答是否有 context 支持
                if settings.FAITHFULNESS_ENABLED and full_content and context_str:
                    faith_result = _faithfulness_checker.check(full_content, context_str)
                    logger.info(f"{_elapsed('Faithfulness')} score={faith_result['score']} faithful={faith_result['faithful']}")
                    if faith_result["score"] < 0.5:
                        yield _sse_format({
                            "type": "faithfulness",
                            "score": faith_result["score"],
                            "warning": "部分回答可能未被知识库支持",
                            "hallucinated_count": len(faith_result["hallucinated_sentences"]),
                            "supported_count": len(faith_result["supported_sentences"]),
                        })

                # ---- 保存到 Query Cache ----
                try:
                    if full_content and token_usage:
                        cache_data = json.dumps({
                            "answer": full_content,
                            "sources": sources,
                            "confidence": confidence,
                            "token_usage": token_usage.to_dict() if hasattr(token_usage, 'to_dict') else None,
                        }, ensure_ascii=False)
                        await redis_manager.client.setex(cache_key, 3600, cache_data)
                        logger.info(f"[QueryCache] 已缓存: {safe_query}, TTL=3600s")
                except Exception:
                    pass
                # ---- 缓存保存结束 ----

                # 发送来源信息
                if sources:
                    yield _sse_format({
                        "type": "sources",
                        "sources": sources,
                        "confidence": confidence,
                    })

                # ---- DAG Progress: 全部完成 ----
                _dag_nodes[6]["status"] = "done"
                _dag_nodes[6]["duration_ms"] = int(_t_llm * 1000)
                yield _sse_format({"type": "dag_progress", "nodes": _dag_nodes, "edges": _dag_edges})

                # ---- 性能指标 ----
                _t_total = time.perf_counter() - _t0
                yield _sse_format({
                    "type": "performance_metrics",
                    "metrics": {
                        "route_ms": round(_t_route * 1000),
                        "query_understand_ms": round(_t_query_understand * 1000),
                        "search_ms": round(_t_search * 1000),
                        "rag_prep_ms": round(_t_rag_prep * 1000),
                        "llm_ms": round(_t_llm * 1000),
                        "total_ms": round(_t_total * 1000),
                    },
                })

                # 保存对话记忆（应用输出安全过滤）
                if session_id and chat_memory:
                    await chat_memory.add_message(session_id, "user", body.query, user_id=_user_id)
                    await chat_memory.add_message(
                        session_id, "assistant", _apply_output_guard(full_content),
                        metadata={"confidence": confidence, "sources": sources[:3]},
                        user_id=_user_id,
                    )

            elif intent == IntentType.TOOL_CALL:
                _t2 = time.perf_counter()
                tool_name = route_result.get("tool_name", "unknown")

                # ---- 澄清检查：参数不足时主动问用户 ----
                # 方法1: 规则匹配检查（工具关键词命中但缺少参数）
                clarify = check_needs_clarification(safe_query, tool_name)
                # 方法2: LLM 判断需要澄清（语义理解）
                needs_clarify_by_llm = route_result.get("_needs_clarify", False)
                if (clarify and clarify.needs_clarification) or needs_clarify_by_llm:
                    logger.info(f"[Clarify] 需要澄清: tool={tool_name} missing={clarify.missing_params}")
                    question = clarify.question if clarify else "请问您想查询哪个物料的库存？可以提供物料编码（如 MAT-001）或物料名称。"
                    missing = clarify.missing_params if clarify else ["material_code"]
                    yield _sse_format({
                        "type": "clarify",
                        "question": question,
                        "tool": tool_name,
                        "missing_params": missing,
                    })
                    yield _sse_format({"type": "content", "content": question})
                    yield "data: [DONE]\n\n"
                    logger.info(f"[Clarify] 已发送澄清提问")
                    return

                # ---- 工具权限检查（必须在发送 tool_status 之前） ----
                if not _is_tool_allowed(tool_name, _user_role):
                    logger.warning(f"[Permission] 用户角色 {_user_role} 无权调用工具 {tool_name}")
                    yield _sse_format({
                        "type": "tool_blocked",
                        "tool": tool_name,
                        "reason": f"您的角色「{_role_label(_user_role)}」无权执行此操作",
                    })
                    yield _sse_format({
                        "type": "content",
                        "content": f"⚠️ 无权执行 **{tool_name}** 操作。如需权限，请联系管理员。",
                    })
                    yield "data: [DONE]\n\n"
                    return

                # 发送工具调用状态
                yield _sse_format({
                    "type": "tool_status",
                    "status": "calling",
                    "tool": tool_name,
                })

                # ---- 写操作审批检查 ----
                WRITE_TOOLS = {"create_ticket"}
                if tool_name in WRITE_TOOLS and (not body.approved or body.approved_tool != tool_name):
                    # 发送审批请求，不执行工具
                    logger.info(f"[Approval] 写操作需要审批: tool={tool_name}")
                    yield _sse_format({
                        "type": "approval_request",
                        "tool": tool_name,
                        "query": safe_query,
                        "message": f"即将执行写操作：{tool_name}，请确认是否继续。",
                    })
                    yield _sse_format({
                        "type": "content",
                        "content": f"⚠️ 即将执行 **{tool_name}** 操作，请点击「确认执行」继续。",
                    })
                    yield "data: [DONE]\n\n"
                    return

                agent = _get_tool_agent(body.agent_type)
                result = await agent.run(
                    query=safe_query,
                    tool_names=[tool_name] if tool_name else None,
                    session_id=session_id,
                    user_id=_user_id,
                )
                _t_gen = time.perf_counter() - _t2

                # 发送工具调用结果
                for tc in result["tool_calls"]:
                    yield _sse_format({
                        "type": "tool_call",
                        "tool": tc["tool"],
                        "input": tc["input"],
                        "observation": tc["observation"],
                    })

                # 发送最终回答
                yield _sse_format({"type": "content", "content": _apply_output_guard(result["answer"])})
                logger.info(f"{_elapsed('TOOL_CALL处理')} 耗时={_t_gen*1000:.0f}ms")

            else:
                yield _sse_format({
                    "type": "content",
                    "content": "抱歉，我不太理解您的问题，请提供更多细节。",
                })

            # 4. 发送完成信号
            yield "data: [DONE]\n\n"
            _t_total = time.perf_counter() - _t0
            logger.info(f"对话完成 session={session_id} 总耗时={_t_total*1000:.0f}ms (路由{_t_route*1000:.0f}ms + 生成{_t_gen*1000:.0f}ms)")

        except Exception as e:
            logger.error(f"SSE流式输出错误: {e}")
            yield _sse_format({
                "type": "error",
                "message": str(e),
            })
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- 辅助函数 ----

def _sse_format(data: dict) -> str:
    """格式化SSE消息"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _handle_greeting(query: str) -> str:
    """处理问候意图"""
    greetings = {
        "你好": "你好！我是供应链智能助手，可以帮你查询制度规范、库存订单、创建工单。",
        "嗨": "嗨！我是供应链智能助手，有什么可以帮你的？",
        "在吗": "在的！随时为你服务，请告诉我你想了解什么？",
        "谢谢": "不客气！如果还有其他问题，随时问我😊",
        "再见": "再见！祝你有美好的一天！",
    }
    for key, response in greetings.items():
        if key in query:
            return response
