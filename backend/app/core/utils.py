"""
SupplyChainRAG - 共享工具函数
============================================================
将分散在多个模块中的重复逻辑提取到此处统一管理。

包含:
  - sigmoid_normalize: 分数归一化 (rag.py / rag_engine.py / evaluator.py)
  - dedup_by_chunk_id: 检索结果按 chunk_id 去重 (rag.py ×2)
  - parse_llm_json: 从 LLM 输出中鲁棒解析 JSON (orchestrator / router / langgraph_agent / evaluate)
  - extract_keywords: 统一关键词提取 (keyword_coverage.py / rag_engine.py CriticEvaluator)
  - sentence_coverage_ratio: 句子级关键词覆盖率计算 (evaluator.py _compute_coverage / _compute_context_recall)
============================================================
"""
import json
import math
import re
import logging
from typing import Union

logger = logging.getLogger(__name__)

# 中文停用词（高频无意义词）— 从 keyword_coverage.py 统一维护
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


# ────────────────────────────────────────────────────────────
# 1. sigmoid_normalize — 将任意实数分数映射到 [0, 1]
# ────────────────────────────────────────────────────────────

def sigmoid_normalize(score: float) -> float:
    """Sigmoid 归一化：将 rerank_score 等任意实数映射到 [0, 1]。

    使用场景:
    - rag.py: rerank_score → confidence
    - rag_engine.py: RAGEngine._calculate_confidence
    - evaluator.py: context_precision 中 top_scores 归一化
    """
    return 1 / (1 + math.exp(-score))


# ────────────────────────────────────────────────────────────
# 2. dedup_by_chunk_id — 检索结果按 chunk_id 去重（保序）
# ────────────────────────────────────────────────────────────

def dedup_by_chunk_id(results: list[dict]) -> list[dict]:
    """按 chunk_id 去重，保留首次出现的条目（维持原始排序）。

    使用场景:
    - rag.py answer() 中合并多查询结果
    - rag.py answer() 中 CRAG 重试合并
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        cid = r.get("chunk_id", "")
        if cid not in seen:
            seen.add(cid)
            unique.append(r)
    return unique


# ────────────────────────────────────────────────────────────
# 3. parse_llm_json — 从 LLM 输出中鲁棒解析 JSON
# ────────────────────────────────────────────────────────────

def parse_llm_json(raw: str) -> Union[dict, list]:
    """从 LLM 输出中鲁棒解析 JSON 对象或数组。

    处理 LLM 常见格式问题：
    - ```json ... ``` 代码块包裹
    - ``` ... ``` 无语言标注的代码块
    - 首尾空白 / 换行符
    - JSON 前后夹杂无关文本
    - 优先使用 raw_decode 解析（避免贪婪正则匹配多个 JSON 对象）

    Raises:
        ValueError: 未找到有效的 JSON 结构
        json.JSONDecodeError: JSON 格式无效且无法恢复
    """
    text = raw.strip()

    # 1. 尝试提取 ```json ... ``` 或 ``` ... ``` 代码块
    code_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if code_match:
        text = code_match.group(1).strip()

    # 2. 优先用 raw_decode（从首个 { 或 [ 开始解析，避免贪婪匹配）
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        idx = text.find(start_char)
        if idx >= 0:
            try:
                parsed, _ = json.JSONDecoder().raw_decode(text, idx)
                return parsed
            except json.JSONDecodeError:
                pass

    # 3. 回退：贪婪正则提取最外层 {} 或 []
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        json_match = re.search(r'\[[\s\S]*\]', text)
    if json_match:
        return json.loads(json_match.group())

    raise ValueError(f"No JSON object or array found in LLM output: {text[:200]}")


# ────────────────────────────────────────────────────────────
# 4. extract_keywords — 统一关键词提取
# ────────────────────────────────────────────────────────────

def extract_keywords(text: str) -> set[str]:
    """从文本中提取关键词（统一实现，合并各模块最优策略）。

    策略：
    - 英文单词（≥2 字符，小写化）
    - 编码标识（MAT-001, PO-20250101, SUP-001 等）
    - 中文分词：优先 jieba，回退 n-gram (2-4 字)
    - 数字
    - 过滤停用词

    统一替代:
    - keyword_coverage.py _extract_keywords
    - rag_engine.py CriticEvaluator.extract_keywords
    """
    keywords: set[str] = set()

    # 英文单词
    en_words = re.findall(r'[a-zA-Z]{2,}', text)
    keywords.update(w.lower() for w in en_words if w.lower() not in _STOP_WORDS)

    # 编码标识 (MAT-001, PO-20250101, SUP-001)
    codes = re.findall(r'[A-Z]+-?\d+', text, re.IGNORECASE)
    keywords.update(c.lower() for c in codes)

    # 中文分词：优先 jieba，回退 n-gram
    try:
        import jieba
        cn_tokens = list(jieba.cut(text))
        cn_tokens = [
            t.strip() for t in cn_tokens
            if len(t.strip()) >= 2
            and re.match(r'[\u4e00-\u9fff]+', t.strip())
            and t.strip() not in _STOP_WORDS
        ]
        keywords.update(cn_tokens)
    except ImportError:
        # 回退：2-4 字 n-gram
        cn_segments = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in cn_segments:
            for n in (4, 3, 2):
                for i in range(len(seg) - n + 1):
                    gram = seg[i:i + n]
                    if gram not in _STOP_WORDS:
                        keywords.add(gram)

    # 数字
    numbers = re.findall(r'\d+', text)
    keywords.update(numbers)

    return keywords


# ────────────────────────────────────────────────────────────
# 5. sentence_coverage_ratio — 句子级关键词覆盖率
# ────────────────────────────────────────────────────────────

def sentence_coverage_ratio(
    sent_keywords: set[str],
    chunk_keywords_list: list[set[str]],
    merged_keywords: set[str],
) -> float:
    """计算单个句子的关键词在上下文中的最大覆盖率。

    对每个句子，检查其关键词是否被 *任意一个* context chunk 覆盖。
    同时检查合并后的全局关键词集合（处理跨 chunk 信息）。
    返回 per-chunk max 和 merged overlap 中较大的值。

    Args:
        sent_keywords: 句子的关键词集合
        chunk_keywords_list: 每个 chunk 的关键词集合列表
        merged_keywords: 所有 chunk 关键词的并集

    Returns:
        最大覆盖率 [0.0, 1.0]

    使用场景:
    - evaluator.py _compute_coverage (threshold 0.25)
    - evaluator.py _compute_context_recall (threshold 0.30)
    """
    if not sent_keywords:
        return 1.0  # 无关键词的短句默认为完全覆盖

    # 逐 chunk 检查最大覆盖率
    max_coverage = 0.0
    for ck in chunk_keywords_list:
        if not ck:
            continue
        overlap = len(sent_keywords & ck) / len(sent_keywords)
        max_coverage = max(max_coverage, overlap)

    # 合并后整体覆盖率（处理跨 chunk 的信息）
    merged_overlap = len(sent_keywords & merged_keywords) / len(sent_keywords)

    return max(max_coverage, merged_overlap)


# ────────────────────────────────────────────────────────────
# 6. split_sentences — 将文本拆分为句子
# ────────────────────────────────────────────────────────────

def split_sentences(text: str) -> list[str]:
    """将文本拆分为句子（支持中英文标点）。

    按中英文句号、问号、感叹号、分号、换行拆分，
    过滤长度 < 2 的片段。

    统一替代 keyword_coverage.py _split_sentences。
    """
    parts = re.split(r'[。！？；\n.!?;]+', text)
    sentences = [p.strip() for p in parts if len(p.strip()) >= 2]
    return sentences if sentences else [text.strip()]
