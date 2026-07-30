"""
keyword coverage guard (在线可信度护栏, 关键词覆盖率).
=============================================================
【功能】在线验证 LLM 回答是否被检索到的 context 支持（毫秒级，无外部依赖）

【重要: 与 RAGAS Faithfulness 的区别】
- 本实现: 关键词覆盖率 (本文件), 在线护栏, 纯 Python, 毫秒级
- RAGAS Faithfulness: NLI 蕴含关系, 离线评估, 需要 NLI 模型, 秒级

【算法】
- 提取回答的关键词集合
- 计算 answer_keywords ∩ context_keywords / answer_keywords
- coverage > 50% → 有支持 (supported)
- coverage < 30% → 幻觉 (hallucination)
- 中间区域 → 不确定 (uncertain)

【面试诚实答法】
"在线用 keyword_coverage 关键词覆盖做轻量护栏（扛得住实时流量），
离线用 RAGAS NLI 做质量评估（0.88 那个分数），
两个是不同的东西，本文件是前者。"
=============================================================
"""
import logging
from typing import Optional

# 向后兼容 re-export — 其他模块 (如 evaluator.py) 可能从本模块导入
from app.core.utils import (  # noqa: F401
    extract_keywords as _extract_keywords,
    split_sentences as _split_sentences,
)

logger = logging.getLogger(__name__)


def _keyword_coverage(answer_keywords: set[str], context_keywords: set[str]) -> float:
    """计算回答关键词在 context 中的覆盖率"""
    if not answer_keywords:
        return 0.0
    matched = answer_keywords & context_keywords
    return len(matched) / len(answer_keywords)


class KeywordCoverageChecker:
    """
    Keyword Coverage 检测器

    用关键词覆盖率的轻量方法判断 LLM 回答是否有 context 支持。
    """

    # 阈值配置
    SUPPORTED_THRESHOLD = 0.5   # 覆盖率 > 50% → 有支持
    HALLUCINATION_THRESHOLD = 0.3  # 覆盖率 < 30% → 幻觉

    def check(self, answer: str, context: str) -> dict:
        """
        检查回答的 faithfulness

        Args:
            answer: LLM 生成的回答
            context: 检索到的上下文（来自知识库）

        Returns:
            {
                "faithful": bool,           # 整体是否有足够支持
                "score": float,             # 整体 faithfulness 分数 (0-1)
                "hallucinated_sentences": [  # 可能是幻觉的句子
                    {"sentence": str, "coverage": float}
                ],
                "supported_sentences": [     # 有支持的句子
                    {"sentence": str, "coverage": float}
                ],
            }
        """
        if not answer or not context:
            return {
                "faithful": False,
                "score": 0.0,
                "hallucinated_sentences": [],
                "supported_sentences": [],
            }

        # 提取 context 的关键词（只需提取一次）
        context_keywords = _extract_keywords(context)

        # 将回答拆成句子
        sentences = _split_sentences(answer)

        hallucinated = []
        supported = []
        total_coverage = 0.0

        for sent in sentences:
            sent_keywords = _extract_keywords(sent)
            coverage = _keyword_coverage(sent_keywords, context_keywords)
            total_coverage += coverage

            entry = {"sentence": sent, "coverage": round(coverage, 3)}

            if coverage < self.HALLUCINATION_THRESHOLD:
                hallucinated.append(entry)
            elif coverage >= self.SUPPORTED_THRESHOLD:
                supported.append(entry)
            # 中间区域：不归入任何一方（不确定）

        # 计算整体分数：所有句子覆盖率的平均值
        score = total_coverage / len(sentences) if sentences else 0.0
        score = round(min(score, 1.0), 3)

        # 整体判定：分数 >= 0.5 认为 faithful
        faithful = score >= self.SUPPORTED_THRESHOLD

        return {
            "faithful": faithful,
            "score": score,
            "hallucinated_sentences": hallucinated,
            "supported_sentences": supported,
        }


# 模块级单例
_keyword_coverage_checker: Optional[KeywordCoverageChecker] = None


def get_keyword_coverage_checker() -> KeywordCoverageChecker:
    """获取 Keyword Coverage 检测器单例"""
    global _keyword_coverage_checker
    if _keyword_coverage_checker is None:
        _keyword_coverage_checker = KeywordCoverageChecker()
    return _keyword_coverage_checker
