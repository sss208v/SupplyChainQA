"""
SmartQA - Query 复杂度分析器

不同复杂度的 query 需要不同深度的检索策略：
- 简单查询（"安全库存公式"）→ 向量检索直接返回，省掉 Reranker/Self-RAG
- 中等查询（"供应商准入流程"）→ 完整 RAG 流程
- 复杂查询（多实体+推理）→ RAG + Query 改写 + Self-RAG

实现方式：LLM 分析 query 的4个维度，输出结构化 JSON。
LLM 不可用时回退到规则（关键词长度）。

简单查询跳过 Reranker 和 Self-RAG（省 8 秒），只有复杂查询才走完整流程。
这样 90% 的常见问题响应更快，复杂问题保证质量。"
"""
import json
import logging
import re
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class QueryAnalysis:
    """Query 复杂度分析结果"""
    complexity: float = 0.5       # 0-1，简单→复杂
    entity_count: int = 1         # 实体数量
    needs_reasoning: bool = False # 是否需要多步推理
    strategy: str = "full"        # light / standard / full
    method: str = "rule"          # rule / llm


# 检索策略定义
STRATEGIES = {
    "light": {
        "description": "轻量检索：仅向量检索，跳过 Reranker 和 Self-RAG",
        "use_reranker": False,
        "use_self_rag": False,
        "use_query_rewrite": False,
        "top_k": 3,
    },
    "standard": {
        "description": "标准检索：向量+BM25，可选 Reranker",
        "use_reranker": True,
        "use_self_rag": False,
        "use_query_rewrite": False,
        "top_k": 5,
    },
    "full": {
        "description": "完整检索：全流程（Reranker + Self-RAG + Query Rewrite）",
        "use_reranker": True,
        "use_self_rag": True,
        "use_query_rewrite": True,
        "top_k": 8,
    },
}


class QueryComplexityAnalyzer:
    """Query 复杂度分析器

    两种分析方式：
    1. LLM 分析（准确，~1s）
    2. 规则回退（快，<1ms）
    """

    # 简单查询特征：短 query，单一实体，无推理词
    SIMPLE_PATTERNS = [
        r"^.{2,15}$",  # 短 query
        r"(公式|定义|是什么|什么意思|含义)",
        r"(查|查询|看看|帮我查)",
    ]

    # 复杂查询特征：多实体，推理词，比较词
    COMPLEX_PATTERNS = [
        r"(对比|比较|分析|评估|为什么|原因|影响|如何改进)",
        r"(和|与|以及|或者).{2,}(和|与|以及|或者)",  # 多实体连接
        r"(如果|假设|要是|倘若)",  # 假设推理
        r"(流程|步骤|方案|策略|制度|规范)",  # 需要结构化回答
    ]

    # 实体关键词（供应链领域）
    ENTITY_KEYWORDS = [
        "物料", "供应商", "采购单", "库存", "工单", "质检",
        "MAT-", "PO-", "SCM-", "ISO", "ERP",
    ]

    ANALYSIS_PROMPT = """分析以下供应链查询的复杂度，返回 JSON：

查询：{query}

分析维度：
1. complexity (0-1): 0=简单查找，1=需要深度推理
2. entity_count: 涉及的实体数量（物料/供应商/订单等）
3. needs_reasoning: 是否需要多步推理或因果分析
4. strategy: "light"/"standard"/"full"

只返回 JSON，不要解释：
{{"complexity": 0.5, "entity_count": 1, "needs_reasoning": false, "strategy": "standard"}}"""

    def __init__(self):
        self._stats = {"light": 0, "standard": 0, "full": 0}

    async def analyze(self, query: str, llm=None) -> QueryAnalysis:
        """分析 query 复杂度

        优先用 LLM 分析，失败回退到规则。
        """
        # 尝试 LLM 分析
        if llm:
            try:
                result = await self._llm_analyze(query, llm)
                if result:
                    self._stats[result.strategy] += 1
                    logger.info(
                        f"[QueryAnalyzer] LLM: complexity={result.complexity:.2f} "
                        f"strategy={result.strategy} entities={result.entity_count}"
                    )
                    return result
            except Exception as e:
                logger.warning(f"[QueryAnalyzer] LLM 分析失败: {e}")

        # 回退到规则
        result = self._rule_analyze(query)
        self._stats[result.strategy] += 1
        logger.info(
            f"[QueryAnalyzer] Rule: complexity={result.complexity:.2f} "
            f"strategy={result.strategy}"
        )
        return result

    async def _llm_analyze(self, query: str, llm) -> Optional[QueryAnalysis]:
        """LLM 分析复杂度"""
        prompt = self.ANALYSIS_PROMPT.format(query=query)
        try:
            response = await llm.ainvoke(prompt)
            text = response.content.strip()
            # 提取 JSON
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                data = json.loads(json_match.group())
                return QueryAnalysis(
                    complexity=max(0, min(1, data.get("complexity", 0.5))),
                    entity_count=max(1, data.get("entity_count", 1)),
                    needs_reasoning=data.get("needs_reasoning", False),
                    strategy=data.get("strategy", "standard"),
                    method="llm",
                )
        except Exception as e:
            logger.warning(f"[QueryAnalyzer] JSON 解析失败: {e}")
        return None

    def _rule_analyze(self, query: str) -> QueryAnalysis:
        """规则分析复杂度（回退方案）"""
        score = 0.5  # 基础分

        # 长度因子：越长越复杂
        length = len(query)
        if length < 10:
            score -= 0.2
        elif length > 30:
            score += 0.15
        elif length > 60:
            score += 0.25

        # 简单模式匹配
        for pattern in self.SIMPLE_PATTERNS:
            if re.search(pattern, query):
                score -= 0.15

        # 复杂模式匹配
        for pattern in self.COMPLEX_PATTERNS:
            if re.search(pattern, query):
                score += 0.15

        # 实体计数
        entity_count = 0
        for kw in self.ENTITY_KEYWORDS:
            if kw in query:
                entity_count += 1
        if entity_count > 2:
            score += 0.15

        # 推理词
        reasoning_words = ["为什么", "原因", "影响", "如何", "怎样", "怎么"]
        needs_reasoning = any(w in query for w in reasoning_words)
        if needs_reasoning:
            score += 0.1

        # 限制范围
        score = max(0, min(1, score))

        # 决定策略
        if score < 0.35:
            strategy = "light"
        elif score > 0.65:
            strategy = "full"
        else:
            strategy = "standard"

        return QueryAnalysis(
            complexity=score,
            entity_count=max(1, entity_count),
            needs_reasoning=needs_reasoning,
            strategy=strategy,
            method="rule",
        )

    def get_strategy_config(self, strategy: str) -> dict:
        """获取策略配置"""
        return STRATEGIES.get(strategy, STRATEGIES["standard"])

    def get_stats(self) -> dict:
        """获取策略使用统计"""
        total = sum(self._stats.values())
        return {
            **self._stats,
            "total": total,
            "distribution": {
                k: f"{v/total*100:.1f}%" if total > 0 else "0%"
                for k, v in self._stats.items()
            },
        }


# 单例
query_analyzer = QueryComplexityAnalyzer()
