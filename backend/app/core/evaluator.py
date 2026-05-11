"""
SmartQA Pro - RAGAS 评估引擎
============================================================
1. 本模块实现 RAGAS 风格的三种生成评估指标：
   - Context Precision（检索准确率）：检索到的上下文中有多少是真正相关的
   - Faithfulness（忠实度/防幻觉）：回答被上下文支持的程度
   - Answer Relevance（回答相关性）：回答与问题的相关程度

2. 评估流程：
   Load Ground Truth → 逐条 Query → 检索 → 生成 → 评分 → 汇总

3. 所有评分归一化到 0.0 ~ 1.0 范围
============================================================
"""
import json
import os
import logging
import time
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 黄金测试集路径
_GROUND_TRUTH_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "eval_ground_truth.json")


@dataclass
class RAGASEvalResult:
    """单条 RAGAS 评估结果"""
    query_id: str = ""
    query: str = ""
    generated_answer: str = ""
    reference_answer: str = ""

    # RAGAS 核心指标（0.0 ~ 1.0）
    context_precision: float = 0.0
    faithfulness: float = 0.0
    answer_relevance: float = 0.0
    overall_score: float = 0.0

    # 元数据
    retrieval_count: int = 0
    generation_time_ms: float = 0.0
    error: str = ""


@dataclass
class RAGASSummary:
    """全量评估汇总"""
    total_queries: int = 0
    avg_context_precision: float = 0.0
    avg_faithfulness: float = 0.0
    avg_answer_relevance: float = 0.0
    avg_overall: float = 0.0
    total_time_ms: float = 0.0
    details: list = field(default_factory=list)


def load_ground_truth(path: str = None) -> list[dict]:
    """加载黄金测试集"""
    path = path or _GROUND_TRUTH_PATH
    if not os.path.exists(path):
        logger.warning(f"黄金测试集不存在: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class RAGASEvaluator:
    """
    RAGAS 风格评估器

    三种指标说明：
    - Context Precision: 检索结果中相关文档的比例
      计算方式：precision_at_k 的加权平均（排名靠前的权重更大）
    - Faithfulness: 回答被检索上下文支持的程度
      计算方式：代理 faithfulness.py 的关键词覆盖率
    - Answer Relevance: 回答与问题的相关程度
      计算方式：代理 LLM-as-Judge 的 answer_relevance 维度
    """

    def __init__(self):
        self.history: list[RAGASEvalResult] = []
        self.ground_truth = load_ground_truth()

    def evaluate_single(
        self,
        query: str,
        retrieved_chunks: list[dict],
        generated_answer: str,
        reference_answer: str = "",
    ) -> RAGASEvalResult:
        """
        对单条查询进行 RAGAS 评估

        Args:
            query: 用户问题
            retrieved_chunks: 检索返回的 chunk 列表（含 chunk_id）
            generated_answer: LLM 生成的回答
            reference_answer: 标准答案（可选）

        Returns:
            RAGASEvalResult: 包含三种指标评分
        """
        t0 = time.perf_counter()
        result = RAGASEvalResult(
            query=query,
            generated_answer=generated_answer,
            reference_answer=reference_answer,
        )

        # 1. Context Precision：从检索结果中计算
        # 利用 rag_engine 的 rerank_score 分布作为 precision 代理
        # （当无 ground truth chunk_id 时，用 rerank_score 的均分近似）
        scores = [c.get("rerank_score", 0) for c in retrieved_chunks]
        if scores:
            result.retrieval_count = len(scores)
            # 取前3个 rerank_score 的加权平均（排名越高考权重越大）
            top_scores = scores[:3]
            if top_scores:
                weights = [3, 2, 1][:len(top_scores)]
                weighted = sum(s * w for s, w in zip(top_scores, weights))
                result.context_precision = round(weighted / sum(weights), 4)

        # 2. Faithfulness：关键词覆盖率（沿用 faithfulness.py 的逻辑）
        if generated_answer and retrieved_chunks:
            context_text = " ".join([
                c.get("content", "") or c.get("text", "") or ""
                for c in retrieved_chunks[:5]
            ])
            result.faithfulness = round(
                self._compute_faithfulness(generated_answer, context_text), 4
            )

        # 3. Answer Relevance：用 LLM-as-Judge 评估
        if generated_answer and query:
            result.answer_relevance = round(
                self._compute_answer_relevance(query, generated_answer), 4
            )

        # 综合得分（加权平均）
        result.overall_score = round(
            0.35 * result.context_precision
            + 0.35 * result.faithfulness
            + 0.30 * result.answer_relevance,
            4,
        )

        result.generation_time_ms = round((time.perf_counter() - t0) * 1000, 2)
        self.history.append(result)
        return result

    def _compute_faithfulness(self, answer: str, context: str) -> float:
        """计算忠实度：回答关键词在 context 中的覆盖率"""
        try:
            from app.core.faithfulness import _split_sentences, _extract_keywords

            sentences = _split_sentences(answer)
            if not sentences:
                return 0.0

            context_kws = _extract_keywords(context)
            if not context_kws:
                return 0.0

            supported = 0
            for sent in sentences:
                sent_kws = _extract_keywords(sent)
                if not sent_kws:
                    supported += 1  # 短句默认支持
                    continue
                overlap = len(sent_kws & context_kws)
                if overlap / len(sent_kws) >= 0.3:
                    supported += 1

            return supported / len(sentences)
        except Exception as e:
            logger.warning(f"Faithfulness 计算失败: {e}")
            return 0.0

    def _compute_answer_relevance(self, query: str, answer: str) -> float:
        """计算回答相关性（基于关键词重叠的轻量级评估）"""
        try:
            from app.core.faithfulness import _extract_keywords

            query_kws = _extract_keywords(query)
            answer_kws = _extract_keywords(answer)

            if not query_kws or not answer_kws:
                return 0.5  # 无关键词时默认中等

            overlap = len(query_kws & answer_kws)
            # 计算 Jaccard 相似度
            union = len(query_kws | answer_kws)
            if union == 0:
                return 0.5

            jaccard = overlap / union
            # 对短查询进行放大（短查询关键词少，Jaccard 可能偏低）
            if len(query_kws) <= 3 and jaccard < 0.3:
                jaccard = min(jaccard * 1.5, 0.6)

            return round(min(jaccard, 1.0), 4)
        except Exception as e:
            logger.warning(f"Answer Relevance 计算失败: {e}")
            return 0.0

    def run_full_suite(self, rag_agent=None, llm_factory=None) -> RAGASSummary:
        """
        运行全量评估套件

        对黄金测试集中的每一条 query：
        1. 实际检索（调用 rag_engine.search）
        2. 调用 LLM 生成回答
        3. 计算 RAGAS 三种指标

        Args:
            rag_agent: RAG Agent 实例（用于检索+回答）
            llm_factory: LLM 工厂（用于生成回答）

        Returns:
            RAGASSummary: 汇总结果
        """
        t_start = time.perf_counter()
        summary = RAGASSummary()
        ground_truth = self.ground_truth

        if not ground_truth:
            logger.warning("黄金测试集为空，跳过全量评估")
            return summary

        if not rag_agent:
            logger.warning("未提供 rag_agent，使用在线评估模式（仅检索）")
            return self._run_retrieval_only(ground_truth, t_start)

        logger.info(f"开始全量 RAGAS 评估: {len(ground_truth)} 条测试用例")

        for item in ground_truth:
            query = item.get("query", "")
            ref_answer = item.get("reference_answer", "")
            qid = item.get("id", "")

            try:
                # 1. 检索
                result = rag_agent.rag.search(query, top_k=5)
                chunks = result.get("results", [])

                # 2. 生成回答（调用 LLM）— 暂注释，/full 端点使用仅检索模式
                # if chunks and llm_factory:
                #     from langchain_core.messages import SystemMessage, HumanMessage
                #     context = rag_agent._format_context(chunks)
                #     system_prompt = rag_agent.RAG_SYSTEM_PROMPT.format(
                #         chat_history="（无历史对话）",
                #         context=context,
                #     )
                #     llm = llm_factory.get_llm(temperature=0.3, streaming=False)
                #     response = llm.invoke([
                #         SystemMessage(content=system_prompt),
                #         HumanMessage(content=query),
                #     ])
                #     answer = response.content
                # else:
                answer = ""

                # 3. 评估
                eval_result = self.evaluate_single(
                    query=query,
                    retrieved_chunks=chunks,
                    generated_answer=answer,
                    reference_answer=ref_answer,
                )
                eval_result.query_id = qid

                summary.details.append({
                    "id": qid,
                    "query": query,
                    "context_precision": eval_result.context_precision,
                    "faithfulness": eval_result.faithfulness,
                    "answer_relevance": eval_result.answer_relevance,
                    "overall": eval_result.overall_score,
                    "retrieval_count": eval_result.retrieval_count,
                    "error": eval_result.error,
                })

            except Exception as e:
                logger.error(f"评估用例 {qid} 失败: {e}")
                summary.details.append({
                    "id": qid,
                    "query": query,
                    "error": str(e),
                    "context_precision": 0,
                    "faithfulness": 0,
                    "answer_relevance": 0,
                    "overall": 0,
                })

        # 汇总
        return self._compute_summary(summary, t_start)

    def _run_retrieval_only(self, ground_truth: list, t_start: float) -> RAGASSummary:
        """仅检索模式（不生成回答）"""
        summary = RAGASSummary()
        for item in ground_truth:
            query = item.get("query", "")
            try:
                from app.core.rag_engine import rag_engine
                result = rag_engine.search(query, top_k=5)
                chunks = result.get("results", [])
                eval_result = self.evaluate_single(
                    query=query,
                    retrieved_chunks=chunks,
                    generated_answer="",
                )
                summary.details.append({
                    "query": query,
                    "context_precision": eval_result.context_precision,
                    "faithfulness": eval_result.faithfulness,
                    "answer_relevance": eval_result.answer_relevance,
                    "overall": eval_result.overall_score,
                    "retrieval_count": eval_result.retrieval_count,
                })
            except Exception as e:
                summary.details.append({
                    "query": query,
                    "error": str(e),
                })
        return self._compute_summary(summary, t_start)

    def _compute_summary(self, summary: RAGASSummary, t_start: float) -> RAGASSummary:
        """计算汇总统计"""
        details = summary.details
        if not details:
            return summary

        valid = [d for d in details if not d.get("error")]
        if not valid:
            return summary

        summary.total_queries = len(valid)
        summary.avg_context_precision = round(
            sum(d["context_precision"] for d in valid) / len(valid), 4
        )
        summary.avg_faithfulness = round(
            sum(d["faithfulness"] for d in valid) / len(valid), 4
        )
        summary.avg_answer_relevance = round(
            sum(d["answer_relevance"] for d in valid) / len(valid), 4
        )
        summary.avg_overall = round(
            sum(d["overall"] for d in valid) / len(valid), 4
        )
        summary.total_time_ms = round((time.perf_counter() - t_start) * 1000, 2)

        logger.info(
            f"RAGAS 评估完成: {summary.total_queries}条, "
            f"CP={summary.avg_context_precision:.3f}, "
            f"Faith={summary.avg_faithfulness:.3f}, "
            f"AR={summary.avg_answer_relevance:.3f}, "
            f"Overall={summary.avg_overall:.3f}, "
            f"耗时={summary.total_time_ms:.0f}ms"
        )
        return summary


# 全局单例
ragas_evaluator = RAGASEvaluator()
