"""
SupplyChainRAG - RAG问答Agent
============================================================
1. RAG = Retrieval-Augmented Generation（检索增强生成）
   核心思想：先检索相关文档，再让LLM基于检索结果生成回答
   好处：减少幻觉、回答有据可查、知识可动态更新

2. 本Agent的完整工作流程：
   Query理解 → 混合检索(向量+BM25) → Reranker精排 → Prompt组装 → LLM生成

3. 三级Query理解策略（来自黑马EduAgent项目）：
   - 明确问题：直接检索
   - 模糊问题：HyDE（假设文档嵌入）扩展语义
   - 宽泛问题：改写成多个子问题并行检索

4. 置信度感知：
   - 高置信度(>0.7)：附带参考来源，直接展示
   - 低置信度(≤0.7)：标注「仅供参考」，提示知识库可能需要补充
============================================================
"""
import asyncio
import logging
import re
from typing import Optional
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.rag_engine import rag_engine, CriticEvaluator, QueryRewriter
from app.core.llm_router import LLMFactory
from app.core.redis_client import chat_memory
from app.config import get_settings
from app.core.llm_relevance import get_self_rag
from app.core.query_analyzer import query_analyzer
from app.core.utils import sigmoid_normalize, dedup_by_chunk_id

logger = logging.getLogger(__name__)
settings = get_settings()


class RAGAgent:
    """
    RAG问答Agent

    工作流程：
    1. 接收用户问题
    2. Query理解（明确/模糊/宽泛）
    3. 混合检索（Milvus向量 + BM25关键词）
    4. BGE-Reranker精排 → 取Top3
    5. 组装Prompt（系统指令 + 检索上下文 + 对话历史 + 用户问题）
    6. LLM生成回答
    7. 后处理（清除AI套话、格式清洗）
    8. 置信度评估 + 参考来源标注
    """

    # ---- Prompt模板 ----
    # 1. 明确告诉LLM"只基于给定的上下文回答"
    # 2. 如果上下文不包含答案，要求LLM诚实说"不知道"
    # 3. 引用来源时标注序号，方便用户溯源
    # 4. 第一句话就要切中要害，禁止铺垫性语句
    RAG_SYSTEM_PROMPT = """你是一个供应链知识库问答助手。请根据参考资料全面、准确地回答问题。

## 核心规则:
0. **仅基于参考资料** - 你的回答必须完全基于下面【参考资料】中的内容。绝对不要使用参考资料之外的领域知识、经验、常识、推测来补充回答。如果参考资料没有提到某个细节，你的回答中也不要有这个细节。
1. **紧贴问题回答** - 直接回答用户问的问题，不要偏离到其他话题。如果问"流程分几步"，回答"分为X步"而不是先介绍背景。
2. **优先基于资料作答** - 只要参考资料中含有与问题相关的信息（哪怕需要综合多条、或仅部分覆盖），就必须据此作答，不得直接回复“暂无相关信息”；仅当参考资料与问题**完全无关**时，才回答“这个问题知识库中暂无相关信息”。
3. **标注引用来源** - 回答中的每一个关键事实、数字、流程都必须标注 [1] [2] 等编号。每一条陈述都必须有引用来源。
4. **禁止编造** - 严禁在回答中添加任何参考资料中没有明确提及的信息、数据、数字、百分比、流程步骤。如果不确定某信息是否在参考资料中，宁可不写。
5. **回答要全面详尽** - 如果参考资料充分，回答必须详尽完整，覆盖参考资料中所有相关细节、数字、流程，不要遗漏也不要添加。

## 引用规则:
- 在回答正文中 标注 [1] [2] 等编号
- 回答结尾列出所有引用
- 引用格式: [编号] 文档名称 — 章节

## 输出规则:
- 第一句就回答问题
- 回答要详尽完整，不要过于简短
- 使用结构化格式（列表、分点）提高可读性

## 当前对话历史:
{chat_history}

## 参考资料:
{context}"""

    # 低置信度时的Prompt（更强调简洁和诚实）
    RAG_LOW_CONFIDENCE_PROMPT = """你是一个专业的知识库问答助手。请严格基于以下检索到的参考资料来回答用户的问题。

## 回答规则：
1. 只基于【参考资料】中的内容回答，不要编造信息，不要添加参考资料中没有的额外内容
2. 只要参考资料含相关信息就据此作答；仅当与问题完全无关时，才说“知识库中暂无相关信息”
3. 同样需要在回答正文中用 [1] [2] [3] 标注引用编号，回答末尾列出引用详情
4. 引用详情格式：每行一条，形如 "[编号] 文档名称 — 对应章节或上下文摘要"
5. 回答必须极度简洁，禁止任何客套话、结语、建议性语句
6. 禁止使用的表达：希望、建议、核实、了解详情、如需进一步、仅供参考、如果有疑问
7. 只输出回答本身和引用来源，不要任何前缀或后缀文字
8. 绝对禁止添加参考资料中未提及的数据和事实

## 当前对话历史：
{chat_history}

## 检索到的参考资料：
{context}"""

    # HyDE Prompt（假设文档嵌入）
    # 模糊查询的向量表示往往与真实文档的向量表示距离较远，
    # 但如果先让LLM"猜"一个答案，这个猜测答案的向量表示
    # 反而更接近真实文档。用猜测答案的向量去做检索，效果更好。
    # 设计原则：短小精悍，只要关键词和术语，不需要完整句式
    HYDE_PROMPT = """请针对以下问题，用2-3句话写一个简短的技术性回答。不需要完全准确，但要包含相关技术术语和关键词。

问题：{query}

简短回答："""

    # 子问题改写Prompt
    SUB_QUERY_PROMPT = """用户提出了一个宽泛的问题，请将其拆分为3-5个具体的子问题，便于分别检索。

原始问题：{query}

请输出子问题列表（每行一个，不要编号）："""

    # 【后处理】AI常见套话模式列表
    # 设计决策：在Prompt层面约束LLM的同时，增加后处理兜底，
    # 形成"Prompt约束 + 后处理清洗"的双重防线，确保输出质量
    AI_ISM_PATTERNS = [
        '根据参考资料',
        '根据检索结果',
        '希望对您有帮助',
        '如有疑问',
        '建议核实',
        '仅供参考',
        '如果您还有其他问题',
        '需要注意的是',
    ]

    # 引用编号正则：匹配 [1] [2] ... [10] 等格式
    _CITATION_PATTERN = re.compile(r'\[(\d+)\]')

    def __init__(self):
        self.rag = rag_engine
        self.logger = logging.getLogger(__name__)

    def _build_prompt(self, context_str: str, chat_history: str = "") -> str:
        """构建 RAG 系统提示词（供 chat_stream_handlers 使用）"""
        return self.RAG_SYSTEM_PROMPT.format(
            chat_history=chat_history or "（无历史对话）",
            context=context_str,
        )

    # ------------------------------------------------------------------
    # 检索管线（单一实现：answer() 与 handlers/rag_answer.py 共用，
    # 避免两套平行的 RAG 流水线各自演化）
    # ------------------------------------------------------------------

    async def prepare_retrieval(self, query: str) -> dict:
        """检索前置：Query理解 + 复杂度分析 + 检索查询准备

        Returns:
            {
                "query_type": str,          # specific/ambiguous/broad
                "rrf_query_type": str,      # precise/default/semantic
                "search_queries": list[str],# 实际检索查询（含 HyDE/子问题）
                "analysis": QueryAnalysis,  # 复杂度分析结果
                "strategy_config": dict,    # 检索策略配置（top_k/use_self_rag 等）
                "adaptive_top_k": int,
                "t_prepare": float,         # 耗时（秒）
            }
        """
        import time as _t
        _t0 = _t.perf_counter()

        query_type = self._classify_query(query)
        rrf_query_type = self._map_rrf_query_type(query_type)
        logger.info(f"Query理解: type={query_type}, rrf_weights={rrf_query_type}, query={query}")

        # 根据复杂度决定检索深度：light/standard/full
        llm_for_analysis = LLMFactory.get_llm(temperature=0, streaming=False)
        analysis = await query_analyzer.analyze(query, llm=llm_for_analysis)
        strategy_config = query_analyzer.get_strategy_config(analysis.strategy)
        logger.info(
            f"Query复杂度: score={analysis.complexity:.2f} strategy={analysis.strategy} "
            f"entities={analysis.entity_count} reasoning={analysis.needs_reasoning} method={analysis.method}"
        )

        search_queries = await self._prepare_queries(query, query_type)
        logger.info(f"检索查询: {search_queries}")

        return {
            "query_type": query_type,
            "rrf_query_type": rrf_query_type,
            "search_queries": search_queries,
            "analysis": analysis,
            "strategy_config": strategy_config,
            "adaptive_top_k": strategy_config.get("top_k", settings.RERANK_TOP_K),
            "t_prepare": _t.perf_counter() - _t0,
        }

    async def execute_retrieval(
        self,
        query: str,
        prep: dict,
        doc_ids: Optional[list[str]] = None,
        visibility_expr: str = "",
    ) -> dict:
        """检索执行：多查询混合检索 → 去重 → CRAG 重试 → LLM 相关性过滤

        同步检索（embedding/pymilvus/reranker）均经 to_thread 隔离。

        Returns:
            {
                "results": list[dict],        # 最终检索结果（已过滤）
                "all_chunks": list[dict],     # LLM 相关性过滤前的全量 chunk（父子文档扩展用）
                "relevance_scores": list,     # LLM 相关性过滤评分（未触发时为空）
                "t_search": float,            # 检索耗时（秒）
            }
        """
        import time as _t
        _t0 = _t.perf_counter()

        rrf_query_type = prep["rrf_query_type"]
        adaptive_top_k = prep["adaptive_top_k"]
        strategy_config = prep["strategy_config"]

        all_results = []
        for sq in prep["search_queries"]:
            result = await asyncio.to_thread(
                self.rag.search, sq,
                top_k=adaptive_top_k, doc_ids=doc_ids,
                visibility_expr=visibility_expr, query_type=rrf_query_type,
            )
            all_results.extend(result.get("results", []))

        # 去重（按chunk_id）
        unique_results = dedup_by_chunk_id(all_results)
        t_search = _t.perf_counter() - _t0

        # ---- CRAG - Corrective RAG 检索质量评估 ----
        # 参考论文: Singh et al. "Agentic RAG" (arXiv:2501.09136) Section 5.4
        # 核心思想: 检索后评估质量，不满意则改写 Query 重试
        if settings.CRAG_ENABLED and unique_results and strategy_config.get("use_crag", True):
            critic_result = CriticEvaluator.evaluate(query, unique_results)
            logger.info(
                f"[CRAG] Critic评估: quality={critic_result['quality']} "
                f"keyword_coverage={critic_result['keyword_coverage']} "
                f"top_score={critic_result['top_score']} "
                f"suggestion={critic_result['suggestion']}"
            )

            if critic_result["needs_retry"]:
                # 改写 Query
                rewritten_query = QueryRewriter.rewrite_for_retry(
                    query, unique_results, critic_result["suggestion"]
                )
                logger.info(f"[CRAG] Query改写: '{query}' -> '{rewritten_query}'")

                # 用改写后的 Query 重新检索（to_thread 隔离同步检索）
                retry_result = await asyncio.to_thread(
                    self.rag.search,
                    rewritten_query, top_k=adaptive_top_k, doc_ids=doc_ids,
                    visibility_expr=visibility_expr, query_type=rrf_query_type,
                )
                retry_results = retry_result.get("results", [])

                if retry_results:
                    # 合并原始结果和重试结果，去重
                    merged_results = dedup_by_chunk_id(unique_results + retry_results)

                    # 按 rerank_score 降序排序
                    merged_results.sort(
                        key=lambda x: x.get("rerank_score", 0), reverse=True
                    )

                    # 再次评估
                    retry_eval = CriticEvaluator.evaluate(query, merged_results)
                    logger.info(
                        f"[CRAG] 重试后评估: quality={retry_eval['quality']} "
                        f"keyword_coverage={retry_eval['keyword_coverage']}"
                    )

                    # 如果重试后质量提升，使用合并结果
                    if retry_eval["quality"] != "low":
                        unique_results = merged_results
                        logger.info(f"[CRAG] 使用重试结果 ({len(unique_results)} 条)")
                    else:
                        logger.info("[CRAG] 重试未改善，保留原始结果")
                else:
                    logger.info("[CRAG] 重试无结果，保留原始结果")

        # 保存全量 chunk（LLM 相关性过滤前）供父子文档扩展使用
        all_chunks = list(unique_results)

        # ---- Reflection - LLM 相关性过滤噪声文档（借鉴 Self-RAG 思想，非论文级）----
        # 参考论文: Singh et al. "Agentic RAG" (arXiv:2501.09136)
        # Agentic 设计模式: Reflection - Agent 检查自己的检索结果质量
        relevance_scores = []
        if (
            settings.LLM_RELEVANCE_ENABLED
            and strategy_config.get("use_self_rag", True)
            and len(unique_results) >= 4
        ):
            try:
                llm_relevance = get_self_rag()
                unique_results, relevance_scores = await llm_relevance.filter_chunks(
                    query, unique_results, LLMFactory
                )
                if relevance_scores:
                    avg_score = sum(s.score for s in relevance_scores) / len(relevance_scores)
                    logger.info(
                        f"[Reflection] LLM相关性过滤完成: {len(unique_results)}条结果, "
                        f"平均相关性={avg_score:.2f}"
                    )
            except Exception as e:
                logger.warning(f"[Reflection] LLM相关性过滤失败，跳过: {e}")
                relevance_scores = []

        return {
            "results": unique_results,
            "all_chunks": all_chunks,
            "relevance_scores": relevance_scores,
            "t_search": t_search,
        }

    async def answer(
        self,
        query: str,
        session_id: Optional[str] = None,
        doc_ids: Optional[list[str]] = None,
        user_id: str = "",
    ) -> dict:
        """
        RAG问答主流程（非流式）：prepare_retrieval → execute_retrieval → LLM生成

        Args:
            query: 用户问题
            session_id: 会话ID（用于对话记忆）
            doc_ids: 限定检索的文档ID列表
            user_id: 用户标识（对话记忆按用户隔离，缺失时落入 anon 空间）

        Returns:
            {
                "answer": str,            # 生成的回答
                "sources": list,          # 参考来源
                "confidence": float,      # 置信度
                "query_type": str,        # 查询理解类型
                "context_used": int,      # 使用的上下文条数
            }
        """
        # ---- Step 1+2: Query理解 + 复杂度分析 + 检索查询准备 ----
        prep = await self.prepare_retrieval(query)
        analysis = prep["analysis"]

        # ---- Step 3: 混合检索 + CRAG + LLM 相关性过滤（单一实现）----
        retrieval = await self.execute_retrieval(query, prep, doc_ids=doc_ids)
        unique_results = retrieval["results"]

        # ---- Step 4: 组装上下文（含父子文档扩展，与流式 handler 一致）----
        context_str, sources = self._format_context(unique_results, all_chunks=retrieval["all_chunks"])
        # 使用 sigmoid 映射将 rerank_score 归一化到 [0,1]，避免原始分数范围不可控
        raw_score = unique_results[0].get("rerank_score", 0.0) if unique_results else 0.0
        confidence = round(sigmoid_normalize(raw_score), 4)

        if not unique_results:
            return {
                "answer": "根据现有知识库，我暂时无法找到与您问题相关的信息。请尝试换个方式提问，或联系管理员补充知识库。",
                "sources": [],
                "confidence": 0.0,
                "query_type": prep["query_type"],
                "context_used": 0,
            }

        # ---- Step 5: 获取对话历史（按 user_id 隔离）----
        chat_history_str = ""
        if session_id and chat_memory:
            chat_history_str = await chat_memory.get_context_string(session_id, user_id=user_id)

        # ---- Step 6: LLM生成回答（置信度感知）----
        # 高置信度用标准prompt，低置信度用严格prompt避免冗余
        if confidence >= settings.CONFIDENCE_THRESHOLD:
            system_prompt = self.RAG_SYSTEM_PROMPT.format(
                chat_history=chat_history_str or "（无历史对话）",
                context=context_str,
            )
        else:
            system_prompt = self.RAG_LOW_CONFIDENCE_PROMPT.format(
                chat_history=chat_history_str or "（无历史对话）",
                context=context_str,
            )

        llm = LLMFactory.get_llm(temperature=0.3, streaming=False)
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=query),
        ])

        # ---- Step 7: 后处理 ----
        # 【设计决策】即使Prompt已经约束了LLM的行为，后处理仍然是必要的：
        # 1. LLM有时会"忘记"Prompt中的约束
        # 2. 不同LLM模型的遵从度不同，后处理提供统一的质量保障
        # 3. 属于防御性编程，确保返回给用户的回答干净整洁
        answer = self._post_process_answer(response.content)

        # ---- Step 8: 置信度评估 ----
        # 置信度已内化到prompt中，LLM不会输出冗余短语
        # 低置信度时仅在返回结构中标记，前端可据此显示不同样式

        # ---- Step 9: 保存对话记忆（按 user_id 隔离）----
        if session_id and chat_memory:
            await chat_memory.add_message(session_id, "user", query, user_id=user_id)
            await chat_memory.add_message(
                session_id, "assistant", answer,
                metadata={"confidence": confidence, "sources": sources[:3]},
                user_id=user_id,
            )

        return {
            "answer": answer,
            "sources": sources,
            "confidence": confidence,
            "query_type": prep["query_type"],
            "context_used": len(unique_results),
            "query_analysis": {
                "complexity": analysis.complexity,
                "strategy": analysis.strategy,
                "entity_count": analysis.entity_count,
                "needs_reasoning": analysis.needs_reasoning,
                "method": analysis.method,
            },
        }

    @staticmethod
    def _map_rrf_query_type(query_type: str) -> str:
        """Map RAGAgent query classification to RRF merge weight profile.

        - 'specific' (tech keywords / codes) → 'precise' → BM25 weight ×1.5
        - 'ambiguous' → 'default' → equal weights (regex fallback handles
          fine-grained semantic/precise detection from query text)
        - 'broad' → 'semantic' → vector weight ×1.5
        """
        mapping = {
            "specific": "precise",
            "ambiguous": "default",
            "broad": "semantic",
        }
        return mapping.get(query_type, "default")

    @staticmethod
    def _post_process_answer(answer: str) -> str:
        """
        LLM回答后处理：清除AI常见套话和格式问题

        【设计决策】后处理是RAG系统质量保障的最后一道防线。
        即使Prompt已经明确禁止了某些表达，LLM仍然可能偶尔违反，
        后处理确保最终输出的一致性和专业性。

        处理步骤：
        1. 保护引用编号 [1][2][3] 不被误删
        2. 移除AI套话短语（如"根据参考资料"、"希望对您有帮助"等）
        3. 移除"答:"、"回答:"等前缀
        4. 清理多余空白和换行
        5. 恢复引用编号
        6. 兜底：如果清洗后为空，返回默认提示
        """
        if not answer or not answer.strip():
            return "知识库中暂无相关信息，请尝试换个方式提问。"

        cleaned = answer

        # Step 1: 保护引用编号 — 用占位符替换，处理完再恢复
        citations = {}
        citation_counter = [0]
        def _save_citation(m):
            key = f"__CITE_{citation_counter[0]}__"
            citations[key] = m.group(0)
            citation_counter[0] += 1
            return key
        cleaned = RAGAgent._CITATION_PATTERN.sub(_save_citation, cleaned)

        # Step 2: 移除AI常见套话
        # 逐个匹配并移除，不区分位置（可能出现在开头、中间或结尾）
        for phrase in RAGAgent.AI_ISM_PATTERNS:
            cleaned = cleaned.replace(phrase, '')

        # Step 3: 移除开头的"答:"或"回答:"前缀
        # 使用正则支持中英文冒号和不同空白
        cleaned = re.sub(r'^(答|回答)\s*[:：]\s*', '', cleaned.strip())

        # Step 4: 清理格式
        # 移除每行的首尾空白
        lines = [line.strip() for line in cleaned.split('\n')]
        # 移除连续空行（保留最多一个空行作为段落分隔）
        cleaned_lines = []
        prev_empty = False
        for line in lines:
            if not line:
                if not prev_empty:
                    cleaned_lines.append('')
                prev_empty = True
            else:
                cleaned_lines.append(line)
                prev_empty = False
        cleaned = '\n'.join(cleaned_lines).strip()

        # Step 5: 恢复引用编号
        for key, value in citations.items():
            cleaned = cleaned.replace(key, value)

        # Step 6: 兜底处理
        # 如果清洗后内容为空（极端情况），返回友好提示而非空字符串
        if not cleaned:
            return "知识库中暂无相关信息，请尝试换个方式提问。"

        return cleaned

    def _classify_query(self, query: str) -> str:
        """
        Query理解：判断问题类型

        - 明确问题：问题具体、关键词明确，如"Milvus怎么创建索引"
        - 模糊问题：问题笼统但能猜到方向，如"向量数据库是什么"
        - 宽泛问题：问题范围太大，如"讲讲AI"

        判断方法：基于问题长度、疑问词、具体关键词等启发式规则
        """
        # 长度很短 + 没有具体关键词 → 宽泛
        if len(query) <= 5 and not any(kw in query for kw in ["什么", "如何", "怎么", "为什么"]):
            return "broad"

        # 包含多个问号或"还有"、"另外"等 → 宽泛
        if query.count("？") > 1 or query.count("?") > 1:
            return "broad"

        # 包含具体技术词 + 疑问词 → 明确
        tech_keywords = [
            "API", "配置", "部署", "安装", "代码", "错误", "报错",
            "参数", "设置", "连接", "使用", "创建", "删除", "修改",
        ]
        if any(kw in query for kw in tech_keywords):
            return "specific"

        # 默认 → 模糊
        return "ambiguous"

    # ---- 同义词扩展表（BM25 检索增强）----
    _SYNONYMS = {
        "安全库存": "安全库存 最低库存量 safety stock",
        "采购": "采购 供应商 供应 purchase supplier",
        "质检": "质检 质量检验 检验 quality inspection IQC",
        "入库": "入库 收货 进货 inbound receiving",
        "出库": "出库 发货 出货 outbound picking",
        "工单": "工单 生产订单 work order ticket",
        "BOM": "BOM 物料清单 bill of materials",
        "在途": "在途 在运 在库 transit",
        "呆滞": "呆滞 滞销 过期 obsolete dead stock",
        "供应商": "供应商 供应商 supplier vendor",
        "库存": "库存 存货 inventory stock",
        "物料": "物料 零件 材料 material part",
        "订单": "订单 采购单 order PO",
        "排产": "排产 生产计划 排程 production scheduling",
        "验收": "验收 检验 收货 inspection acceptance",
        # 供应链核心术语扩展（优化 Context Recall）
        "物流": "物流 运输 配送 发货 logistics shipping",
        "时效": "时效 时限 周期 SLA turnaround 检测期限",
        "AQL": "AQL 抽样标准 GB/T 2828.1 合格质量水平 抽检比例",
        "标准成本": "标准成本 成本核算 直接人工 制造费用 standard costing",
        "MPS": "MPS 主生产计划 排程 生产计划 master production schedule 月度编制 滚动计划",
        "编码": "编码 物料编码 物料代码 part number",
        "黑名单": "黑名单 淘汰 不合格 退出 blacklist 禁入 重新准入 重新申请",
        "审批": "审批 审核 核准 批准 approval",
        "绩效": "绩效 考核 评估 KPI performance",
        "风险": "风险 预警 异常 应急 risk alert",
        "抽检": "抽检 全检 检验比例 抽样检验 100%全检 AQL抽样 sampling",
        "检验": "检验 检测 测试 实验室 检测报告 质检 inspection testing lab",
        "缺陷": "缺陷 不合格 致命缺陷 严重缺陷 主要缺陷 critical major defect",
        "预警": "预警 触发条件 阈值 警报 黄色预警 紧急采购 alert trigger threshold",
        "流程": "流程 步骤 编制流程 截止日期 月度编制 滚动编制 会签 时限 rolling process deadline",
        "标准": "标准 要求 规范 准则 SLA 服务水平 等级标准 分数线 standard",
        "四步法": "四步法 出库 发货 领料 FIFO 拣货 复核 发货登记 outbound picking",
        "实验室": "实验室 检测 周期 工作日 检验 报告 lab testing turnaround",
    }

    def _expand_synonyms(self, query: str) -> str:
        """轻量级同义词扩展：匹配关键词后追加同义词，增强 BM25 召回"""
        expansions = []
        for key, expansion in self._SYNONYMS.items():
            if key in query:
                expansions.append(expansion)
        if expansions:
            return f"{query} {' '.join(expansions)}"
        return query

    async def _prepare_queries(self, query: str, query_type: str) -> list[str]:
        """
        根据查询类型准备检索查询

        - specific: 直接用原始query检索，最高效
        - ambiguous: 用HyDE生成"假设答案"，用假设答案的向量检索
        - broad: 拆分成多个子问题，分别检索后合并结果
        """
        if query_type == "specific":
            # 明确问题 → 直接检索 + 同义词扩展（对所有查询启用，提升召回率）
            expanded = self._expand_synonyms(query)
            # 去重：用 dict.fromkeys 保持顺序并去重，避免相同查询重复检索
            queries = list(dict.fromkeys([query, expanded] if expanded != query else [query]))
            self.logger.info(f"[_prepare_queries] query_type=specific → {len(queries)}个查询")
            return queries

        elif query_type == "ambiguous":
            # 模糊问题 → 用HyDE生成假设文档，增强语义检索
            hyde_query = await self._generate_hyde(query)
            self.logger.info(f"[_prepare_queries] query_type=ambiguous → HyDE: {hyde_query[:80]}...")
            return [hyde_query]

        else:
            # 宽泛问题 → 拆分为多个子问题，扩大检索覆盖范围
            sub_queries = await self._generate_sub_queries(query)
            self.logger.info(f"[_prepare_queries] query_type=broad → {len(sub_queries)}个子问题: {sub_queries}")
            return sub_queries

    async def _generate_hyde(self, query: str) -> str:
        """HyDE：生成假设文档用于语义检索"""
        try:
            llm = LLMFactory.get_llm(temperature=0.5, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content=self.HYDE_PROMPT.format(query=query)),
                HumanMessage(content="请生成回答："),
            ])
            return response.content
        except Exception as e:
            logger.warning(f"HyDE生成失败: {e}, 退回原始查询")
            return query

    async def _generate_sub_queries(self, query: str) -> list[str]:
        """子问题改写：将宽泛问题拆分为多个具体子问题"""
        try:
            llm = LLMFactory.get_llm(temperature=0.3, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content=self.SUB_QUERY_PROMPT.format(query=query)),
                HumanMessage(content="请输出子问题："),
            ])
            sub_queries = [line.strip() for line in response.content.strip().split("\n") if line.strip()]
            return sub_queries[:settings.MAX_SUB_QUERIES]  # 子问题扇出上限（config 可调）
        except Exception as e:
            logger.warning(f"子问题改写失败: {e}")
            return [query]

    @staticmethod
    def _format_context(results: list[dict], all_chunks: list[dict] = None) -> tuple[str, list[dict]]:
        """
        将检索结果格式化为Prompt中的上下文文本

        检索阶段用小chunk精确匹配（512字符），
        生成阶段用大chunk提供完整上下文（同文档+同章节的所有chunk合并）。

        这样做的好处：
        - 检索精度高（小chunk语义集中）
        - 生成质量高（大chunk上下文完整）
        """
        context_parts = []
        sources = []

        # 构建 section 索引：(source, section_title) -> [chunks]
        section_index = {}
        if all_chunks:
            for chunk in all_chunks:
                key = (chunk.get("source", ""), chunk.get("section_title", ""))
                if key not in section_index:
                    section_index[key] = []
                section_index[key].append(chunk)

        for i, result in enumerate(results, 1):
            content = result.get("content", "")
            source = result.get("source", "未知来源")
            page = result.get("page_num", 0)
            section_title = result.get("section_title", "")

            # ---- 父子文档扩展：用同章节的完整内容 ----
            if all_chunks and section_title:
                key = (source, section_title)
                section_chunks = section_index.get(key, [])
                if len(section_chunks) > 1:
                    # 合并同章节的所有chunk，去重
                    seen_contents = {content}
                    merged_parts = [content]
                    for sc in section_chunks:
                        sc_content = sc.get("content", "")
                        if sc_content and sc_content not in seen_contents:
                            seen_contents.add(sc_content)
                            merged_parts.append(sc_content)
                    if len(merged_parts) > 1:
                        content = "\n".join(merged_parts)
                        logger.debug(f"[父子文档] {source}/{section_title}: 合并{len(merged_parts)}个chunk")

            # 截断过长内容
            if len(content) > 2048:
                content = content[:2048] + "..."

            # 构建位置描述
            location_parts = []
            if section_title:
                location_parts.append(section_title)
            if page and page > 0:
                location_parts.append(f"第{page}页")
            location = "，".join(location_parts) if location_parts else ""

            context_parts.append(
                f"[{i}] 文档: {source}" + (f"（{location}）" if location else "") + f"\n{content}"
            )

            sources.append({
                "index": i,
                "source": source,
                "page": page,
                "section": section_title,
                "snippet": content[:200],
                "score": result.get("rerank_score", 0),
            })

        context_str = "\n\n---\n\n".join(context_parts)
        return context_str, sources


# 全局单例
rag_agent = RAGAgent()
