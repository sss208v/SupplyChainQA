"""
SmartQA Pro - Faithfulness 检测模块
=============================================================
【功能】验证 LLM 回答是否被检索到的 context 支持

采用轻量级 NLI 方法（纯 Python，无外部依赖）：
- 将回答拆成句子
- 对每个句子提取关键词，检查 context 中的关键词覆盖率
- 覆盖率 > 50% → 有支持
- 覆盖率 < 30% → 幻觉
- 中间区域 → 不确定
=============================================================
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 中文停用词（高频无意义词）
_STOP_WORDS = frozenset({
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "可以", "能", "可能", "应该", "需要", "已",
    "把", "被", "从", "对", "与", "或", "但", "而", "等", "中", "为",
    "及", "以", "于", "其", "这个", "那个", "这些", "那些", "所", "之",
    "吗", "呢", "吧", "啊", "哦", "嗯", "请", "谢谢", "还", "又", "再",
    "如果", "因为", "所以", "虽然", "但是", "并且", "而且", "或者",
    "以及", "并且", "根据", "通过", "使用", "进行", "提供", "包括",
})


def _split_sentences(text: str) -> list[str]:
    """将文本拆分为句子（支持中英文标点）"""
    # 按中英文句号、问号、感叹号、分号拆分
    parts = re.split(r'[。！？；\n.!?;]+', text)
    sentences = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2:  # 忽略过短的片段
            sentences.append(p)
    return sentences if sentences else [text.strip()]


def _extract_keywords(text: str) -> set[str]:
    """
    从文本中提取关键词（轻量级实现）

    策略：
    - 英文：按空格/标点拆词，过滤停用词和短词
    - 中文：提取 2-4 字的 n-gram，过滤停用词
    """
    keywords = set()

    # 提取英文单词
    en_words = re.findall(r'[a-zA-Z]{2,}', text)
    for w in en_words:
        wl = w.lower()
        if wl not in _STOP_WORDS and len(wl) >= 2:
            keywords.add(wl)

    # 提取中文字符序列（连续中文）
    cn_segments = re.findall(r'[\u4e00-\u9fff]+', text)
    for seg in cn_segments:
        # 单字一般意义不大，提取 2-4 gram
        for n in (4, 3, 2):
            for i in range(len(seg) - n + 1):
                gram = seg[i:i + n]
                if gram not in _STOP_WORDS:
                    keywords.add(gram)

    return keywords


def _keyword_coverage(answer_keywords: set[str], context_keywords: set[str]) -> float:
    """计算回答关键词在 context 中的覆盖率"""
    if not answer_keywords:
        return 0.0
    matched = answer_keywords & context_keywords
    return len(matched) / len(answer_keywords)


class FaithfulnessChecker:
    """
    Faithfulness 检测器

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
_faithfulness_checker: Optional[FaithfulnessChecker] = None


def get_faithfulness_checker() -> FaithfulnessChecker:
    """获取 Faithfulness 检测器单例"""
    global _faithfulness_checker
    if _faithfulness_checker is None:
        _faithfulness_checker = FaithfulnessChecker()
    return _faithfulness_checker
