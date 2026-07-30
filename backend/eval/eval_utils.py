# -*- coding: utf-8 -*-
"""
评估工具模块 — 共享函数
============================================================
提供给所有 eval/ 脚本复用的工具函数：
1. rebuild_bm25_from_milvus() — 从 Milvus 重建 BM25 索引
2. clean_response() — 清洗 RAG 回答中的干扰文本
3. truncate_contexts() — 截断检索上下文避免 RAGAS prompt 超限
4. strip_citation_tail() — 剥离答案尾部引用列表块（送 judge 前口径清洗）
5. strip_non_factual_frame() — 剥离开场铺垫/冗余收尾等非事实框架句（送 judge 前口径清洗）
============================================================
"""
import re
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def rebuild_bm25_from_milvus(rag_engine, milvus_manager) -> int:
    """
    从 Milvus 重建 BM25 索引。

    背景：BM25 索引是内存结构，后端启动时通过 upload_all_knowledge.py 填充。
    但 standalone 评估脚本直接 import rag_engine 时，BM25 是空的。
    本函数从 Milvus 读取所有文档，重建 BM25 索引。

    Args:
        rag_engine: RAGEngine 单例
        milvus_manager: MilvusManager 单例

    Returns:
        int: 索引的 chunk 总数
    """
    if rag_engine.bm25._bm25 is not None and len(rag_engine.bm25._chunks) > 0:
        logger.info(f"BM25 已有索引: {len(rag_engine.bm25._chunks)} chunks, 跳过重建")
        return len(rag_engine.bm25._chunks)

    logger.info("BM25 索引为空，从 Milvus 重建...")
    milvus_manager.connect()
    milvus_manager.create_collection()
    c = milvus_manager.collection
    c.load()

    # 读取所有文档
    results = c.query(
        expr="id > 0",
        output_fields=["doc_id", "chunk_id", "content", "source", "page_num", "security_group"],
        limit=10000,
    )

    # 按 doc_id 分组
    doc_chunks: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        doc_chunks[r["doc_id"]].append({
            "chunk_id": r["chunk_id"],
            "content": r["content"],
            "source": r["source"],
            "page_num": r.get("page_num", 0),
            "security_group": r.get("security_group", ["admin"]),
        })

    # 重建 BM25 索引
    for doc_id, chunks in doc_chunks.items():
        security_group = chunks[0].get("security_group", ["admin"])
        rag_engine.bm25.index_documents(doc_id, chunks, security_group=security_group)

    total = len(rag_engine.bm25._chunks)
    logger.info(f"BM25 重建完成: {len(doc_chunks)} docs, {total} chunks")
    return total


def clean_response(response: str) -> str:
    """
    清洗 RAG 回答，去除导致 RAGAS 误判的前缀后缀。

    清洗内容：
    - 「仅供参考」标记
    - 置信度警告行
    - [来源X] 引用标记（RAGAS 不识别这些标记）
    - 多余空行
    """
    if not response or response.startswith("ERROR"):
        return response
    response = response.replace("「仅供参考」", "").strip()
    response = re.sub(r"⚠️ 该回答的置信度较低.*?建议核实信息准确性。", "", response, flags=re.DOTALL)
    response = re.sub(r"\[来源\d+\]", "", response)
    response = re.sub(r"\n{3,}", "\n\n", response)
    return response.strip()


def truncate_contexts(contexts: list, max_chars: int = 500, max_count: int = 2) -> list:
    """
    截断检索上下文，避免 RAGAS prompt 超过 Judge LLM context window。

    Args:
        contexts: 检索到的上下文列表
        max_chars: 每条上下文最大字符数
        max_count: 最多保留的上下文条数
    """
    truncated = []
    for ctx in contexts[:max_count]:
        if len(ctx) > max_chars:
            ctx = ctx[:max_chars] + "..."
        truncated.append(ctx)
    return truncated


# 引用尾部行："[n] 文档名 — 章节" 形式；引用块标题行："引用："/"引用:"
_CITE_ITEM_RE = re.compile(r"^\s*\[\d+\]")
_CITE_HEADER_RE = re.compile(r"^\s*引用[：:]\s*$")


def strip_citation_tail(response: str) -> str:
    """
    剥离答案末尾的引用列表块（仅用于送 RAGAS judge 的口径清洗）。

    背景：RAGAS Faithfulness 把答案拆成逐条陈述对照 contexts 判定，
    尾部"引用：[n] 文档名 — 章节"是元信息而非事实陈述，常被判不支持
    而系统性压低分数。产品端答案不受影响（引用展示是前端功能）。

    策略：从尾部向上扫描，剥离连续的 "[n] ..." 行（含空行）及紧邻的
    "引用："标题行；遇到正文行立即停止。正文行内的 [n] 标注保留不动。
    """
    if not response:
        return response
    lines = response.rstrip().split("\n")
    i = len(lines)
    removed_cite = False
    while i > 0:
        line = lines[i - 1]
        if not line.strip():
            i -= 1
            continue
        if _CITE_ITEM_RE.match(line):
            removed_cite = True
            i -= 1
            continue
        if _CITE_HEADER_RE.match(line):
            removed_cite = True
            i -= 1
        break
    if not removed_cite:
        return response.strip()
    return "\n".join(lines[:i]).strip()


# ---- P0-A: 非事实框架句剥离（送 judge 口径清洗，产品端答案不受影响）----
# 背景：开场铺垫/冗余收尾会被 RAGAS Faithfulness 当独立陈述判"无上下文支撑"而压分。
# 严格保守：只削答案首尾的框架/套话，绝不动中间的事实、数字、引用编号；
# 含"暂无相关信息/未明确提及"等无答案标记时整体跳过（那是无答案题的正式答案）。
_NOINFO_MARKERS = ("暂无相关信息", "未明确提及", "并未提及", "没有明确提及", "无法提供")
_OPENING_FRAME_RE = re.compile(
    r"^(?:以下是[^：:\n]{0,30}[：:]|为您解答如下[：:]?|现回答如下[：:]?|解答如下[：:]?|回答如下[：:]?)\s*"
)
_LEADING_MARKER_RE = re.compile(
    r"^(?:根据(?:以上|上述)?(?:参考|提供的)?资料[，,]|综上所述[，,]|总的来说[，,])\s*"
)
_CLOSING_FILLER_RE = re.compile(
    r"(?:希望(?:以上|上述)?[^。.\n]{0,20}(?:帮助|参考)[。.！!]?|"
    r"如有(?:其他|任何)?(?:疑问|问题)[^。.\n]{0,15}(?:咨询|联系|提问)?[。.！!]?)\s*$"
)


def strip_non_factual_frame(text: str) -> str:
    """剥离非事实框架句（开场铺垫/冗余收尾），仅用于送 RAGAS judge 的口径清洗。

    与 strip_citation_tail 配合使用（先剥引用块、再剥框架）。严格保守：
    - 含"暂无相关信息/未明确提及"等无答案标记 → 整体原样返回（那是正式答案，不能削）
    - 只削答案开头的框架句/行首话语标记与结尾套话，中间的事实、数字、引用编号一律不动
    """
    if not text or text.startswith("ERROR"):
        return text
    if any(m in text for m in _NOINFO_MARKERS):
        return text.strip()
    t = text.strip()
    t = _OPENING_FRAME_RE.sub("", t).strip()
    t = _LEADING_MARKER_RE.sub("", t).strip()
    t = _CLOSING_FILLER_RE.sub("", t).strip()
    return t
