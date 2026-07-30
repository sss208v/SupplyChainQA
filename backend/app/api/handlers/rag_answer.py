"""
RAG_ANSWER intent handler — 完整 RAG Pipeline

涵盖了查询理解、多路检索、LLM相关性过滤、CLIP多模态检索、
置信度路由、LLM生成、缓存保存等完整逻辑。
"""
import json
import hashlib
import logging
import time
import asyncio
from typing import AsyncGenerator, Optional
from app.api.chat_helpers import sse_event, _sse_format, _build_rag_demo_answer, ChatRequest
from app.agents.rag import RAGAgent
from app.core.llm_router import LLMFactory, TokenUsage, MODEL_PRICING
from app.core.circuit_breaker import CircuitOpenError
from app.core.redis_client import ChatMemory
from app.core.keyword_coverage import get_keyword_coverage_checker
from app.core.graph_engine import extract_entities
from app.core.neo4j_client import Neo4jClient
from app.config import get_settings
from app.core.milvus_client import MilvusManager
from app.core.query_analyzer import QueryComplexityAnalyzer
from app.core.confidence_router import get_confidence_router
from app.core.utils import sigmoid_normalize
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

# 模块级单例
_keyword_coverage_checker = get_keyword_coverage_checker()

__all__ = ["handle_rag_answer"]


async def handle_rag_answer(
    safe_query: str,
    user_role: str,
    session_id: str,
    body: ChatRequest,
    langfuse_callbacks: Optional[list],
    t0: float,
    t_route: float,
    user_id: str,
    rag_agent: RAGAgent,
    milvus: MilvusManager,
    neo4j: Neo4jClient,
    memory: Optional[ChatMemory],
    query_analyzer: QueryComplexityAnalyzer,
) -> AsyncGenerator[str, None]:
    """处理 RAG 问答意图 — 完整 RAG Pipeline

    包含: 查询理解 → 多路检索 → LLM相关性过滤 → CLIP → 置信度路由 → LLM生成 → 缓存
    """
    settings = get_settings()

    # ---- DAG Progress: 意图路由完成 ----
    dag_nodes = [
        {"name": "意图路由", "status": "done", "duration_ms": int(t_route * 1000)},
        {"name": "查询理解", "status": "running", "duration_ms": 0},
        {"name": "复杂度分析", "status": "pending", "duration_ms": 0},
        {"name": "向量检索", "status": "pending", "duration_ms": 0},
        {"name": "BM25检索", "status": "pending", "duration_ms": 0},
        {"name": "Reranker精排", "status": "pending", "duration_ms": 0},
        {"name": "答案生成", "status": "pending", "duration_ms": 0},
    ]
    dag_edges = [
        {"from": 0, "to": 1}, {"from": 1, "to": 2}, {"from": 2, "to": 3},
        {"from": 3, "to": 4}, {"from": 4, "to": 5}, {"from": 5, "to": 6},
    ]
    yield sse_event("dag_progress", nodes=dag_nodes, edges=dag_edges)

    # 构建检索上下文 — 查询理解/复杂度分析/检索/CRAG/LLM相关性过滤 全部复用
    # RAGAgent 的单一实现（prepare_retrieval + execute_retrieval），
    # 避免与 /chat/ask 非流式链路维护两套平行的 RAG 流水线
    _t2 = time.perf_counter()
    prep = await rag_agent.prepare_retrieval(safe_query)
    _t_query_understand = prep["t_prepare"]
    _analysis = prep["analysis"]
    _strategy_config = prep["strategy_config"]

    yield sse_event(
        "query_analysis",
        complexity=round(_analysis.complexity, 2),
        strategy=_analysis.strategy,
        entity_count=_analysis.entity_count,
        needs_reasoning=_analysis.needs_reasoning,
        method=_analysis.method,
    )

    # 构建可见性过滤表达式
    _vis_expr = milvus.build_visibility_expr(user_role, body.doc_ids)

    # ---- DAG Progress: 查询理解完成，复杂度分析完成，开始检索 ----
    dag_nodes[1]["status"] = "done"
    dag_nodes[1]["duration_ms"] = int(_t_query_understand * 1000)
    dag_nodes[2]["status"] = "done"
    dag_nodes[2]["duration_ms"] = 0  # 复杂度分析与查询理解同步
    dag_nodes[3]["status"] = "running"
    dag_nodes[4]["status"] = "running"
    yield sse_event("dag_progress", nodes=dag_nodes, edges=dag_edges)

    # ---- 检索执行：多查询混合检索 + CRAG 重试 + LLM 相关性过滤（单一实现）----
    _t3 = time.perf_counter()
    retrieval = await rag_agent.execute_retrieval(
        safe_query, prep, visibility_expr=_vis_expr
    )
    unique_results = retrieval["results"]
    _t_search = retrieval["t_search"]
    seen = {r.get("chunk_id", "") for r in unique_results}

    # ---- DAG Progress: 检索完成，开始精排 ----
    dag_nodes[3]["status"] = "done"
    dag_nodes[3]["duration_ms"] = int(_t_search * 1000)
    dag_nodes[4]["status"] = "done"
    dag_nodes[4]["duration_ms"] = int(_t_search * 1000)
    dag_nodes[5]["status"] = "running"
    yield sse_event("dag_progress", nodes=dag_nodes, edges=dag_edges)

    # 保存所有chunk用于父子文档扩展（LLM 相关性过滤前的全量）
    all_chunks = retrieval["all_chunks"]

    # ---- LLM 相关性过滤结果事件（过滤逻辑已在 execute_retrieval 内执行）----
    if retrieval["relevance_scores"]:
        yield sse_event(
            "llm_relevance",
            scores=[{"chunk_id": s.chunk_id, "score": round(s.score, 2), "reason": s.reason} for s in retrieval["relevance_scores"]],
            filtered_count=len(unique_results),
        )

    # ---- 先格式化上下文，再检测冲突 ----
    context_str, sources = rag_agent._format_context(unique_results, all_chunks=all_chunks)
    # 置信度与非流式 answer() 保持同一口径：sigmoid 归一化到 [0,1]
    # （confidence_router 的 0.3/0.6/0.7 阈值与前端展示均基于归一化值）
    _raw_top = unique_results[0].get("rerank_score", 0.0) if unique_results else 0.0
    confidence = round(sigmoid_normalize(_raw_top), 4) if unique_results else 0.0

    # ---- 冲突检测：多源数据矛盾标记 ----
    all_conflicts = []
    try:
        all_conflicts = rag_agent.rag._detect_conflicts(unique_results)
    except Exception as e:
        logger.warning(f"[Conflict] 冲突检测失败: {e}")
    if all_conflicts:
        yield sse_event(
            "conflicts",
            conflicts=all_conflicts,
            message=f"检测到 {len(all_conflicts)} 处数据冲突，已标记供参考",
        )
        conflict_text = "\n\n[数据冲突提示]\n" + "\n".join(
            f"- {c['entity']}: 存在 {c['values']} 等不同数值，来源不同文档，请综合判断"
            for c in all_conflicts
        )
        context_str = (context_str or "") + conflict_text

    # ---- CLIP 多模态图像检索 ----
    clip_images = []
    if settings.CLIP_ENABLED:
        try:
            from app.core.multimodal_embedding import clip_engine
            _t_clip = time.perf_counter()
            clip_text_vec = clip_engine.encode_text(safe_query)
            clip_images = milvus.search_images(
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
                yield sse_event(
                    "image_search",
                    count=len(clip_images),
                    duration_ms=int(_t_clip_elapsed * 1000),
                    images=[
                        {"image_id": img["image_id"], "description": img.get("description", "")[:200]}
                        for img in clip_images
                    ],
                )
                logger.info(
                    f"[CLIP图像检索] 命中{len(clip_images)}张, 耗时={_t_clip_elapsed*1000:.0f}ms"
                )
        except Exception as e:
            logger.warning(f"[CLIP] 图像检索失败: {e}")

    _t_rerank = time.perf_counter() - _t3 - _t_search

    # ---- 三层置信度路由 ----
    conf_router = get_confidence_router()
    decision = conf_router.decide(confidence, safe_query)
    logger.info(f"[置信度路由] confidence={confidence:.3f} tier={decision.tier} strategy={decision.strategy}")

    yield sse_event(
        "confidence_decision",
        tier=decision.tier,
        strategy=decision.strategy,
        confidence=confidence,
        description=decision.description,
    )

    if decision.strategy == "rewrite" and 0.3 < confidence < 0.6 and _strategy_config.get("use_query_rewrite", True):
        # 中置信度：改写 query 重新检索
        rewrites = await conf_router.rewrite_query(safe_query, LLMFactory)
        if rewrites:
            for rw in rewrites[:2]:
                rw_result = await asyncio.to_thread(
                    rag_agent.rag.search, rw, top_k=3, visibility_expr=_vis_expr
                )
                for r in rw_result.get("results", []):
                    cid = r.get("chunk_id", "")
                    if cid not in seen:
                        seen.add(cid)
                        unique_results.append(r)
            # 图谱融合
            try:
                entities = extract_entities(safe_query)
                if entities and neo4j.is_connected:
                    matched = set()
                    for v in entities.values():
                        matched.update(v)
                    if matched:
                        unique_results = rag_agent.rag.fuse_with_graph(
                            unique_results, matched,
                            alpha=settings.GRAPH_FUSION_ALPHA,
                            beta=settings.GRAPH_FUSION_BETA,
                        )
            except Exception as e:
                logger.debug(f"[Graph] 图谱融合失败（静默降级）: {e}")

            context_str, sources = rag_agent._format_context(unique_results, all_chunks=all_chunks)
            if unique_results:
                # 与主路径同口径：取最高 rerank_score 后 sigmoid 归一化
                confidence = round(sigmoid_normalize(max(r.get("rerank_score", 0) for r in unique_results)), 4)
            logger.info(f"[QueryRewrite] 改写后检索结果={len(unique_results)} 新confidence={confidence:.3f}")

    elif decision.strategy == "web_search":
        # 低置信度：调用 MiniMax Web Search
        yield sse_event(
            "web_search",
            status="searching",
            query=safe_query,
            message="该问题超出知识库覆盖范围，正在搜索外部信息...",
        )
        web_results = await conf_router.web_search(safe_query, api_key=settings.MINIMAX_API_KEY)
        if web_results:
            web_context = conf_router.format_web_results_for_context(web_results)
            context_str = context_str + "\n\n" + web_context
            yield sse_event(
                "web_search",
                status="completed",
                results_count=len(web_results),
                message=f"已从外部搜索获取 {len(web_results)} 条补充信息",
            )
            logger.info(f"[WebSearch] 补充了 {len(web_results)} 条外部结果")
        else:
            yield sse_event(
                "web_search",
                status="no_results",
                message="外部搜索未找到相关信息",
            )

    # 获取对话历史
    chat_history_str = ""
    if session_id and memory:
        chat_history_str = await memory.get_context_string(session_id, user_id=user_id)

    # 构建Prompt
    system_prompt = rag_agent.RAG_SYSTEM_PROMPT.format(
        chat_history=chat_history_str or "（无历史对话）",
        context=context_str,
    )
    _t_rag_prep = time.perf_counter() - _t2
    logger.info(f"[RAG预处理] 检索结果={len(unique_results)} 耗时={_t_rag_prep*1000:.0f}ms")

    # ---- DAG Progress: 精排完成，开始生成 ----
    dag_nodes[5]["status"] = "done"
    dag_nodes[5]["duration_ms"] = max(int(_t_rerank * 1000), 1)
    dag_nodes[6]["status"] = "running"
    yield sse_event("dag_progress", nodes=dag_nodes, edges=dag_edges)

    # 真实token级流式输出
    _t3 = time.perf_counter()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=safe_query),
    ]

    full_content = ""
    chunk = None
    if settings.DEMO_MODE:
        demo_answer = _build_rag_demo_answer(safe_query, unique_results, context_str)
        full_content = demo_answer
        yield sse_event("content", content=demo_answer)
    else:
        try:
            async for chunk in LLMFactory.astream(messages, callbacks=langfuse_callbacks):
                if chunk.content:
                    full_content += chunk.content
                    yield sse_event("content", content=chunk.content)
        except CircuitOpenError as e:
            logger.warning(f"[Chat] 熔断器打开，LLM 暂不可用: {e}")
            yield sse_event(
                "error",
                message=f"LLM 服务暂时过载（{e.provider}），预计 {e.remaining_seconds:.0f}s 后恢复，请稍后重试",
                detail="circuit_open",
            )
        except Exception as e:
            logger.error(f"[Chat] LLM流式调用失败（所有retry已耗尽）: {type(e).__name__}: {e}")
            yield sse_event(
                "error",
                message="服务暂时不可用，请稍后重试",
                detail=f"{type(e).__name__}",
            )

    # 提取token用量
    token_usage = None
    if chunk is not None and hasattr(chunk, '_token_usage') and chunk._token_usage:
        token_usage = chunk._token_usage
    # 如果API没返回usage，用内容长度估算
    if not token_usage or token_usage.total_tokens == 0:
        est_completion = len(full_content) * 2  # 中文约2token/字
        est_prompt = len(system_prompt) * 2 + len(safe_query) * 2
        _active_model = LLMFactory._get_model_name(settings.LLM_PROVIDER)
        pricing = MODEL_PRICING.get(_active_model, {})
        est_cost = (est_prompt / 1_000_000) * pricing.get("input", 0) + (est_completion / 1_000_000) * pricing.get("output", 0)
        token_usage = TokenUsage(
            prompt_tokens=est_prompt,
            completion_tokens=est_completion,
            total_tokens=est_prompt + est_completion,
            cost_yuan=est_cost,
            model=_active_model,
            provider=settings.LLM_PROVIDER,
        )
    yield sse_event("token_usage", usage=token_usage.to_dict())
    _t_llm = time.perf_counter() - _t3
    if token_usage:
        logger.info(f"[LLM流式生成] tokens={token_usage.total_tokens} (in={token_usage.prompt_tokens} out={token_usage.completion_tokens}) 费用=¥{token_usage.cost_yuan:.4f} 耗时={_t_llm*1000:.0f}ms")
    else:
        logger.info(f"[LLM流式生成] token数≈{len(full_content)} 耗时={_t_llm*1000:.0f}ms")
    _t_gen = _t_rag_prep + _t_llm

    # Keyword Coverage 检测
    if settings.COVERAGE_ENABLED and full_content and context_str:
        faith_result = _keyword_coverage_checker.check(full_content, context_str)
        logger.info(f"[Keyword Coverage] score={faith_result['score']} faithful={faith_result['faithful']}")
        if faith_result["score"] < 0.5:
            yield sse_event(
                "coverage",
                score=faith_result["score"],
                warning="部分回答可能未被知识库支持",
                hallucinated_count=len(faith_result["hallucinated_sentences"]),
                supported_count=len(faith_result["supported_sentences"]),
            )

    # ---- 保存到 Query Cache ----
    try:
        if full_content and token_usage:
            from app.core.redis_client import redis_manager as _rm
            cache_input = f"{safe_query}:{user_role}"
            cache_key = f"query_cache:{hashlib.md5(cache_input.encode()).hexdigest()}"
            cache_data = json.dumps({
                "answer": full_content,
                "sources": sources,
                "confidence": confidence,
                "token_usage": token_usage.to_dict() if hasattr(token_usage, 'to_dict') else None,
            }, ensure_ascii=False)
            await _rm.client.setex(cache_key, settings.QUERY_CACHE_TTL, cache_data)
            logger.info(f"[QueryCache] 已缓存: {safe_query}, TTL={settings.QUERY_CACHE_TTL}s")
    except Exception as e:
        logger.debug(f"[QueryCache] 缓存保存失败: {e}")

    # 发送来源信息
    if sources:
        yield sse_event("sources", sources=sources, confidence=confidence)

    # ---- DAG Progress: 全部完成 ----
    dag_nodes[6]["status"] = "done"
    dag_nodes[6]["duration_ms"] = int(_t_llm * 1000)
    yield sse_event("dag_progress", nodes=dag_nodes, edges=dag_edges)

    # ---- 性能指标 ----
    _t_total = time.perf_counter() - t0
    yield sse_event(
        "performance_metrics",
        metrics={
            "route_ms": round(t_route * 1000),
            "query_understand_ms": round(_t_query_understand * 1000),
            "search_ms": round(_t_search * 1000),
            "rag_prep_ms": round(_t_rag_prep * 1000),
            "llm_ms": round(_t_llm * 1000),
            "total_ms": round(_t_total * 1000),
        },
    )

    # 保存对话记忆
    if session_id and memory:
        await memory.add_message(session_id, "user", safe_query, user_id=user_id)
        await memory.add_message(
            session_id, "assistant", full_content,
            metadata={"confidence": confidence, "sources": sources[:3]},
            user_id=user_id,
        )
