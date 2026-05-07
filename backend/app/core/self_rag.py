"""
SmartQA - Self-RAG 检索结果过滤

【学习要点】
Self-RAG 的核心思想：不让 LLM 盲目使用所有检索到的文档，
而是先让 LLM 判断每个文档是否真正支持回答，过滤掉不相关的。

流程：
1. 检索到 N 个 chunk
2. 对每个 chunk，LLM 判断相关性（0-1 分）
3. 过滤掉低于阈值的 chunk
4. 只用相关 chunk 生成回答

面试话术："传统 RAG 把所有检索结果都丢给 LLM，不管是否相关。
Self-RAG 让 LLM 自己判断哪些文档有用，过滤掉噪音，
减少幻觉，提升答案质量。"
"""
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RelevanceScore:
    """文档相关性评分"""
    chunk_id: str
    score: float  # 0-1
    reason: str   # 判断理由


class SelfRAGFilter:
    """Self-RAG 检索结果过滤器"""

    RELEVANCE_THRESHOLD = 0.3  # 低于此分数的 chunk 被过滤

    async def filter_chunks(
        self,
        query: str,
        chunks: list[dict],
        llm_factory,
    ) -> tuple[list[dict], list[RelevanceScore]]:
        """过滤检索结果，只保留相关 chunk

        Args:
            query: 用户查询
            chunks: 检索到的 chunk 列表 [{chunk_id, content, source, ...}]
            llm_factory: LLM 工厂

        Returns:
            (filtered_chunks, scores): 过滤后的 chunk 和评分详情
        """
        if not chunks or len(chunks) <= 1:
            # 只有 0-1 个 chunk，不需要过滤
            return chunks, []

        from langchain_core.messages import SystemMessage, HumanMessage

        # 构建评估 prompt：一次评估所有 chunk（减少 LLM 调用次数）
        chunk_texts = []
        for i, chunk in enumerate(chunks):
            source = chunk.get("source", "未知")
            content = chunk.get("content", "")[:300]  # 截断避免太长
            chunk_texts.append(f"[文档{i+1}] 来源: {source}\n内容: {content}")

        chunks_block = "\n\n".join(chunk_texts)

        eval_prompt = f"""你是文档相关性评估专家。请判断以下文档片段与用户问题的相关性。

用户问题：{query}

检索到的文档：
{chunks_block}

请对每个文档评分（0-1）：
- 0.8-1.0：高度相关，直接包含答案
- 0.5-0.7：部分相关，提供背景信息
- 0.3-0.4：弱相关，可能有用
- 0.0-0.2：不相关，应该过滤掉

请严格按以下JSON数组格式输出，不要输出其他内容：
[{{"doc": 1, "score": 0.9, "reason": "直接回答了问题"}}, ...]"""

        try:
            llm = llm_factory.get_llm(temperature=0.0, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content="你是文档相关性评估专家。只输出JSON数组。"),
                HumanMessage(content=eval_prompt),
            ])

            import json
            import re

            # 解析 LLM 返回的 JSON
            content = response.content.strip()
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if not json_match:
                logger.warning(f"[SelfRAG] LLM 返回格式错误: {content[:100]}")
                return chunks, []

            eval_results = json.loads(json_match.group())

            # 构建评分映射
            scores = []
            for item in eval_results:
                doc_idx = item.get("doc", 0) - 1  # 1-indexed → 0-indexed
                score = item.get("score", 0.5)
                reason = item.get("reason", "")
                if 0 <= doc_idx < len(chunks):
                    scores.append(RelevanceScore(
                        chunk_id=chunks[doc_idx].get("chunk_id", ""),
                        score=score,
                        reason=reason,
                    ))

            # 过滤：保留 score >= threshold 的 chunk
            score_map = {s.chunk_id: s for s in scores}
            filtered = []
            for chunk in chunks:
                cid = chunk.get("chunk_id", "")
                s = score_map.get(cid)
                if s and s.score >= self.RELEVANCE_THRESHOLD:
                    filtered.append(chunk)
                elif s:
                    logger.info(f"[SelfRAG] 过滤: {cid} score={s.score:.2f} reason={s.reason}")

            # 如果全部被过滤，保留 top-1（避免空结果）
            if not filtered and chunks:
                logger.warning("[SelfRAG] 所有chunk被过滤，保留top-1")
                filtered = [chunks[0]]
                scores[0].score = 0.3  # 标记为勉强可用

            logger.info(f"[SelfRAG] {len(chunks)}→{len(filtered)} chunks (阈值={self.RELEVANCE_THRESHOLD})")
            return filtered, scores

        except Exception as e:
            logger.warning(f"[SelfRAG] 过滤失败，保留原始结果: {e}")
            return chunks, []


# 单例
_self_rag: Optional[SelfRAGFilter] = None

def get_self_rag() -> SelfRAGFilter:
    global _self_rag
    if _self_rag is None:
        _self_rag = SelfRAGFilter()
    return _self_rag
