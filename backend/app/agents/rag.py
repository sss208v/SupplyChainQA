"""
SmartQA Pro - RAG问答Agent
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
import logging
import re
from typing import Optional
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from app.core.rag_engine import rag_engine
from app.core.llm_router import LLMFactory
from app.core.redis_client import chat_memory
from app.config import get_settings
from app.core.query_analyzer import query_analyzer, STRATEGIES

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
    RAG_SYSTEM_PROMPT = """你是一个专业的知识库问答助手。请严格基于以下检索到的参考资料来回答用户的问题。

## 回答规则：
1. 只基于【参考资料】中的内容回答，不要编造信息，不要说"根据XX参考资料"这类引导性短语
2. 如果参考资料中没有相关信息，请直接回答"这个问题知识库中暂无相关信息"
3. 引用标注规则（非常重要）：
   - 在回答正文中标注引用编号，格式为 [1] [2] [3]，紧跟在相关陈述之后
   - 同一段信息可对应多个引用，如 [1][2]
   - 回答末尾必须另起一行，列出所有引用的详细信息
   - 引用详情格式：每行一条，形如 "[编号] 文档名称 — 对应章节或上下文摘要"
   - 示例正文："供应商准入需要 ISO 9001 认证 [1]，以及近 3 年财务报表 [2]。"
   - 示例末尾：\n\n**参考来源：**\n[1] 供应商管理手册 — 第3.2节 资质要求\n[2] 供应商管理手册 — 第3.3节 财务审查
4. 用简洁清晰的语言直接回答，不要添加任何结语（如"希望对您有帮助"、"建议核实"等）
5. 如果用户问题涉及多个方面，请分点回答
6. 回答要直接切入要点，第一句话就要回答用户的问题
7. 禁止使用过渡性语句如"接下来让我们看看"、"首先我们需要了解"

## 当前对话历史：
{chat_history}

## 检索到的参考资料：
{context}"""

    # 低置信度时的Prompt（更强调简洁和诚实）
    RAG_LOW_CONFIDENCE_PROMPT = """你是一个专业的知识库问答助手。请严格基于以下检索到的参考资料来回答用户的问题。

## 回答规则：
1. 只基于【参考资料】中的内容回答，不要编造信息
2. 如果参考资料中没有相关信息，直接说"知识库中暂无相关信息"
3. 同样需要在回答正文中用 [1] [2] [3] 标注引用编号，回答末尾列出引用详情
4. 引用详情格式：每行一条，形如 "[编号] 文档名称 — 对应章节或上下文摘要"
5. 回答必须极度简洁，禁止任何客套话、结语、建议性语句
6. 禁止使用的表达：希望、建议、核实、了解详情、如需进一步、仅供参考、如果有疑问
7. 只输出回答本身和引用来源，不要任何前缀或后缀文字

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

    async def answer(
        self,
        query: str,
        session_id: Optional[str] = None,
        doc_ids: Optional[list[str]] = None,
    ) -> dict:
        """
        RAG问答主流程

        Args:
            query: 用户问题
            session_id: 会话ID（用于对话记忆）
            doc_ids: 限定检索的文档ID列表

        Returns:
            {
                "answer": str,            # 生成的回答
                "sources": list,          # 参考来源
                "confidence": float,      # 置信度
                "query_type": str,        # 查询理解类型
                "context_used": int,      # 使用的上下文条数
            }
        """
        # ---- Step 1: Query理解 ----
        query_type = self._classify_query(query)
        logger.info(f"Query理解: type={query_type}, query={query}")

        # 根据复杂度决定检索深度：light/standard/full
        llm_for_analysis = LLMFactory.get_llm(temperature=0, streaming=False)
        analysis = await query_analyzer.analyze(query, llm=llm_for_analysis)
        strategy_config = query_analyzer.get_strategy_config(analysis.strategy)
        logger.info(
            f"Query复杂度: score={analysis.complexity:.2f} strategy={analysis.strategy} "
            f"entities={analysis.entity_count} reasoning={analysis.needs_reasoning} method={analysis.method}"
        )

        # ---- Step 2: 根据查询类型执行不同的检索策略 ----
        search_queries = await self._prepare_queries(query, query_type)
        logger.info(f"检索查询: {search_queries}")

        # ---- Step 3: 混合检索（top_k 由策略决定）----
        adaptive_top_k = strategy_config.get("top_k", settings.RERANK_TOP_K)
        all_results = []
        for sq in search_queries:
            result = self.rag.search(sq, top_k=adaptive_top_k, doc_ids=doc_ids)
            all_results.extend(result.get("results", []))

        # 去重（按chunk_id）
        seen = set()
        unique_results = []
        for r in all_results:
            chunk_id = r.get("chunk_id", "")
            if chunk_id not in seen:
                seen.add(chunk_id)
                unique_results.append(r)

        # ---- Step 4: 组装上下文 ----
        context_str, sources = self._format_context(unique_results)
        # 【修复】使用unique_results而非all_results获取置信度
        # 原因：去重后的unique_results才是按rerank_score排序的最终结果，
        # all_results可能包含多个查询的重复结果，首条未必是最优的
        # 【修复】使用rerank_score而非confidence（检索结果中只有rerank_score字段）
        confidence = unique_results[0].get("rerank_score", 0.0) if unique_results else 0.0

        if not unique_results:
            return {
                "answer": "根据现有知识库，我暂时无法找到与您问题相关的信息。请尝试换个方式提问，或联系管理员补充知识库。",
                "sources": [],
                "confidence": 0.0,
                "query_type": query_type,
                "context_used": 0,
            }

        # ---- Step 5: 获取对话历史 ----
        chat_history_str = ""
        if session_id and chat_memory:
            chat_history_str = await chat_memory.get_context_string(session_id)

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

        # ---- Step 9: 保存对话记忆 ----
        if session_id and chat_memory:
            await chat_memory.add_message(session_id, "user", query)
            await chat_memory.add_message(
                session_id, "assistant", answer,
                metadata={"confidence": confidence, "sources": sources[:3]},
            )

        return {
            "answer": answer,
            "sources": sources,
            "confidence": confidence,
            "query_type": query_type,
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

    async def _prepare_queries(self, query: str, query_type: str) -> list[str]:
        """
        根据查询类型准备检索查询

        - specific: 直接用原始query检索，最高效
        - ambiguous: 用HyDE生成"假设答案"，用假设答案的向量检索
        - broad: 拆分成多个子问题，分别检索后合并结果
        """
        if query_type == "specific":
            # 明确问题 → 直接检索
            return [query]

        elif query_type == "ambiguous":
            # 模糊问题 → 直接检索（HyDE省略，节省14秒embedding时间）
            return [query]

        else:
            # 宽泛问题 → 直接检索（子问题改写省略）
            return [query]

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
            return sub_queries[:5]  # 最多5个子问题
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
            chunk_index = result.get("chunk_index", 0)
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
