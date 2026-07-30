"""
SupplyChainRAG - Critic 评估器与查询改写器 (Agentic RAG / CRAG)

提供 CriticEvaluator 和 QueryRewriter 两个类，实现 Corrective RAG (CRAG) 流程：
- CriticEvaluator：检索后用轻量级规则评估文档质量（关键词覆盖率、rerank 分数等）
- QueryRewriter：当检索质量不足时，改写 Query 用于重试

参考论文: Singh et al. "Agentic Retrieval-Augmented Generation" (arXiv:2501.09136)
"""
import logging
import re

from app.core.utils import extract_keywords as _utils_extract_keywords

logger = logging.getLogger(__name__)


class CriticEvaluator:
    """Agentic RAG - Corrective RAG (CRAG) Critic
    
    参考论文: Singh et al. "Agentic Retrieval-Augmented Generation" (arXiv:2501.09136)
    Section 5.4: Corrective RAG
    
    核心思想: 检索后用 Critic 评估文档质量，不满意则改写 Query 重试。
    使用轻量级规则评估（无 LLM 调用），避免额外延迟。
    
    评估维度:
    1. 关键词覆盖率 - Query 中的关键词在检索结果中被覆盖的比例
    2. 最高 Rerank 分数 - 检索结果的最高相关性分数
    3. 结果数量 - 检索到的有效结果数量
    """

    HIGH_THRESHOLD = 0.6
    LOW_THRESHOLD = 0.3

    @staticmethod
    def extract_keywords(text: str) -> set:
        """提取查询关键词 — 委托给 app.core.utils.extract_keywords 统一实现"""
        return _utils_extract_keywords(text)

    @classmethod
    def evaluate(cls, query: str, results: list[dict]) -> dict:
        """评估检索结果质量"""
        if not results:
            return {
                "quality": "low",
                "keyword_coverage": 0.0,
                "top_score": 0.0,
                "result_count": 0,
                "needs_retry": True,
                "suggestion": "no_results",
            }

        query_keywords = cls.extract_keywords(query)
        if not query_keywords:
            keyword_coverage = 0.5
        else:
            all_text = " ".join(r.get("content", "") for r in results[:5])
            result_keywords = cls.extract_keywords(all_text)
            matched = query_keywords & result_keywords
            keyword_coverage = len(matched) / len(query_keywords) if query_keywords else 0.0

        top_score = max((r.get("rerank_score", 0) for r in results), default=0.0)
        valid_count = sum(1 for r in results if r.get("rerank_score", 0) > 0)

        if keyword_coverage >= cls.HIGH_THRESHOLD and top_score > 0.3:
            quality = "high"
            needs_retry = False
            suggestion = "direct_generate"
        elif keyword_coverage >= cls.LOW_THRESHOLD or top_score > 0.1:
            quality = "medium"
            needs_retry = True
            suggestion = "rewrite_query"
        else:
            quality = "low"
            needs_retry = True
            suggestion = "expand_search"

        return {
            "quality": quality,
            "keyword_coverage": round(keyword_coverage, 3),
            "top_score": round(top_score, 3),
            "result_count": valid_count,
            "needs_retry": needs_retry,
            "suggestion": suggestion,
        }


class QueryRewriter:
    """Agentic RAG - Query Rewriter for CRAG"""

    @staticmethod
    def rewrite_for_retry(query: str, original_results: list[dict], suggestion: str) -> str:
        """改写 Query 用于重试"""
        if suggestion == "expand_search":
            expanded = query
            expanded = re.sub(r'(是多少|怎么|如何|什么|哪些|为什么|请告诉我|帮我查)', '', expanded)
            expanded = re.sub(r'\s+', ' ', expanded).strip()
            return expanded if expanded else query
        
        elif suggestion == "rewrite_query":
            query_keywords = CriticEvaluator.extract_keywords(query)
            if original_results:
                top_content = original_results[0].get("content", "")
                result_keywords = CriticEvaluator.extract_keywords(top_content)
                missing_keywords = result_keywords - query_keywords
                extra = list(missing_keywords)[:2]
                if extra:
                    return query + " " + " ".join(extra)
        
        return query
