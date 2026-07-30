# -*- coding: utf-8 -*-
"""
RRF 权重真实调优脚本（llama.cpp 作为相关性裁判）
====================================================
使用本地 llama.cpp 服务对检索结果做相关性评分，网格搜索最优 RRF 权重。

前置条件：
1. Docker 基础设施已启动（Milvus / Redis / Postgres 等）
2. llama.cpp server 已启动在 http://localhost:8080/v1
3. 知识库已导入（BM25 索引已建立或可从 Milvus 重建）

用法：
    python backend/scripts/tune_rrf_weights_llama.py
    python backend/scripts/tune_rrf_weights_llama.py --dataset backend/eval/eval_raw_data_comprehensive.json --max-queries 10
    python backend/scripts/tune_rrf_weights_llama.py --candidates "1.0,1.5,2.0" --top-k 5 --judge-model Qwen3-14B
"""
import sys
import os
import json
import re
import argparse
import logging
import math
from itertools import product
from datetime import datetime
from typing import Optional

# 确保 backend 在 path 中
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

from langchain_core.messages import SystemMessage, HumanMessage

from app.config import get_settings
from app.core.llm_router import LLMFactory
from app.core.rag_engine import rag_engine
from app.core.milvus_client import milvus_manager
from eval.eval_utils import rebuild_bm25_from_milvus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()

DEFAULT_DATASET = os.path.join(SCRIPT_DIR, "..", "eval", "eval_raw_data_comprehensive.json")

JUDGE_PROMPT = """你是一名检索相关性评估专家。请根据用户查询，判断下面这段上下文对回答该查询的相关程度。

查询：{query}

上下文：
{context}

请只回复一个 0-10 的整数分数：
- 10：直接包含答案
- 7-9：高度相关，包含关键信息
- 4-6：部分相关
- 1-3：弱相关
- 0：完全无关

只输出一个数字，不要解释。"""


def detect_query_type(query: str) -> str:
    """与 RAG engine 一致的 query_type 检测规则（含实体归一化）。"""
    if not query:
        return "default"
    from app.core.rag.engine import RAGEngine
    normalized = RAGEngine._normalize_query_entities(query)
    has_semantic = bool(re.search(r"怎么|如何|什么|哪些|为什么|介绍|说明", query))
    has_precise = bool(re.search(
        r'[A-Z]{2,}-?\d{3,}|\d{4,}|MAT-\d+|PO-\d+|SUP-\d+', normalized
    ))
    if has_precise and not has_semantic:
        return "precise"
    if has_semantic and not has_precise:
        return "semantic"
    return "default"


def load_dataset(path: str, max_queries: Optional[int] = None):
    """加载评估数据集，格式为 eval_raw_data_comprehensive.json。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据集不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = []
    for item in data:
        q = item.get("user_input", "")
        ref = item.get("reference", "")
        if not q or not ref:
            continue
        records.append({
            "query": q,
            "query_type": detect_query_type(q),
            "reference": ref,
        })
        if max_queries and len(records) >= max_queries:
            break

    # 注：带精确 ID（PO/MAT/SUP 等）的查询在真实系统中应走数据库/API，
    # 不属于 RAG 检索调优范围，因此不再自动补充 synthetic precise 样本。
    precise_count = sum(1 for r in records if r["query_type"] == "precise")
    logger.info(f"加载 {len(records)} 条评估数据（含 precise {precise_count} 条）")
    return records


def set_weights(precise_bm25: float, semantic_vector: float, default: float = 1.0):
    """动态修改 settings 中的 RRF 权重。"""
    settings.RRF_BM25_WEIGHT_PRECISE = precise_bm25
    settings.RRF_VECTOR_WEIGHT_SEMANTIC = semantic_vector
    settings.RRF_BM25_WEIGHT_DEFAULT = default
    settings.RRF_VECTOR_WEIGHT_DEFAULT = default


class LlamaRelevanceJudge:
    """使用 llama.cpp（通过 LLMRouter/DeepSeek 配置）评估上下文相关性。"""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.0):
        self.model = model
        self.temperature = temperature
        self._cache: dict[str, float] = {}
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = LLMFactory.get_llm(
                provider=settings.LLM_PROVIDER,
                model=self.model,
                temperature=self.temperature,
            )
        return self._llm

    def _cache_key(self, query: str, context: str) -> str:
        # 简单哈希缓存，避免重复调用 LLM
        return f"{hash(query) & 0xFFFFFFFF}_{hash(context) & 0xFFFFFFFF}"

    def judge(self, query: str, context: str) -> float:
        """返回 0-1 之间的相关性分数。"""
        key = self._cache_key(query, context)
        if key in self._cache:
            return self._cache[key]

        prompt = JUDGE_PROMPT.format(query=query, context=context[:2000])
        try:
            llm = self._get_llm()
            resp = llm.invoke([
                SystemMessage(content="你是一名检索相关性评估专家。"),
                HumanMessage(content=prompt),
            ])
            text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
            # 提取第一个数字
            match = re.search(r"\b(\d{1,2})(\.\d+)?\b", text)
            if match:
                score = float(match.group(1)) / 10.0
            else:
                logger.warning(f"无法从 LLM 响应解析分数: {text[:200]}")
                score = 0.0
        except Exception as e:
            logger.error(f"llama.cpp 相关性判断失败: {e}")
            score = 0.0

        score = max(0.0, min(1.0, score))
        self._cache[key] = score
        return score


def compute_dcg(relevances: list[float]) -> float:
    """计算 DCG，relevances 按检索结果顺序排列。"""
    return sum((2 ** rel - 1) / math.log2(i + 2) for i, rel in enumerate(relevances))


def evaluate_weights(
    weights: tuple[float, float],
    dataset: list[dict],
    judge: LlamaRelevanceJudge,
    top_k: int = 5,
) -> dict:
    """对一组 RRF 权重评估整个数据集。"""
    import math

    precise_bm25, semantic_vector = weights
    set_weights(precise_bm25, semantic_vector)

    # 清除 RAG 查询缓存，确保不同权重组合重新检索
    rag_engine._query_cache.clear()

    mrr_sum = 0.0
    ndcg_sum = 0.0
    precision_sum = 0.0
    query_count = 0
    details = []

    for item in dataset:
        query = item["query"]
        qt = item["query_type"]

        try:
            result = rag_engine.search(query, top_k=top_k, query_type=qt)
            chunks = result.get("results", [])[:top_k]
        except Exception as e:
            logger.error(f"检索失败 [{query[:30]}...]: {e}")
            continue

        if not chunks:
            continue

        relevances = []
        for chunk in chunks:
            ctx = chunk.get("content", "") or chunk.get("text", "")
            rel = judge.judge(query, ctx)
            relevances.append(rel)

        # MRR: 第一个 relevant（>=0.5）的位置倒数
        mrr = 0.0
        for i, rel in enumerate(relevances):
            if rel >= 0.5:
                mrr = 1.0 / (i + 1)
                break

        # NDCG
        ideal = sorted(relevances, reverse=True)
        dcg = compute_dcg(relevances)
        idcg = compute_dcg(ideal)
        ndcg = dcg / idcg if idcg > 0 else 0.0

        # Precision@K: 相关 chunk 比例
        precision = sum(1 for r in relevances if r >= 0.5) / len(relevances)

        mrr_sum += mrr
        ndcg_sum += ndcg
        precision_sum += precision
        query_count += 1

        details.append({
            "query": query,
            "query_type": qt,
            "top_k": top_k,
            "mrr": round(mrr, 4),
            "ndcg": round(ndcg, 4),
            "precision": round(precision, 4),
            "relevances": [round(r, 2) for r in relevances],
        })

    if query_count == 0:
        return {
            "avg_mrr": 0.0,
            "avg_ndcg": 0.0,
            "avg_precision": 0.0,
            "queries": 0,
            "details": details,
        }

    return {
        "avg_mrr": round(mrr_sum / query_count, 4),
        "avg_ndcg": round(ndcg_sum / query_count, 4),
        "avg_precision": round(precision_sum / query_count, 4),
        "queries": query_count,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="使用 llama.cpp 对 RRF 权重做真实调优")
    parser.add_argument("--dataset", type=str, default=DEFAULT_DATASET, help="评估数据集路径")
    parser.add_argument("--max-queries", type=int, default=10, help="最多评估的查询条数")
    parser.add_argument(
        "--candidates",
        type=str,
        default="1.0,1.5,2.0",
        help="权重候选值，逗号分隔（同时用于 precise_bm25 和 semantic_vector）",
    )
    parser.add_argument("--top-k", type=int, default=5, help="评估 Top-K 检索结果")
    parser.add_argument("--judge-model", type=str, default=None, help="llama.cpp 裁判模型名（默认使用 settings.DEEPSEEK_MODEL）")
    parser.add_argument("--rebuild-bm25", action="store_true", help="强制从 Milvus 重建 BM25 索引")
    parser.add_argument("--output", type=str, default=None, help="报告输出 JSON 路径")
    parser.add_argument("--disable-reranker", action="store_true", help="关闭重排序，单独观察 RRF 权重影响")
    args = parser.parse_args()

    if args.disable_reranker:
        settings.RERANKER_ENABLED = False
        logger.info("已关闭重排序模型，仅评估 RRF 融合效果")

    # 重建 BM25（如果需要）
    if args.rebuild_bm25 or (rag_engine.bm25._bm25 is None or len(rag_engine.bm25._chunks) == 0):
        logger.info("检查/重建 BM25 索引...")
        try:
            total = rebuild_bm25_from_milvus(rag_engine, milvus_manager)
            logger.info(f"BM25 索引 chunk 数: {total}")
        except Exception as e:
            logger.error(f"BM25 重建失败: {e}")
            logger.warning("继续执行，但如果索引为空，检索结果可能为空")

    # 加载数据与裁判模型
    dataset = load_dataset(args.dataset, args.max_queries)
    judge = LlamaRelevanceJudge(model=args.judge_model)

    candidates = [float(x.strip()) for x in args.candidates.split(",")]
    logger.info(f"权重候选值: {candidates}, Top-K: {args.top_k}, 查询数: {len(dataset)}")

    # 网格搜索
    results = []
    best_score = -1.0
    best_weights = None

    for precise_bm25, semantic_vector in product(candidates, repeat=2):
        logger.info(f"评估权重: precise_bm25={precise_bm25}, semantic_vector={semantic_vector}")
        metrics = evaluate_weights(
            (precise_bm25, semantic_vector),
            dataset,
            judge,
            top_k=args.top_k,
        )

        # 综合分数：MRR 0.4 + NDCG 0.4 + Precision 0.2
        combined = (
            0.4 * metrics["avg_mrr"]
            + 0.4 * metrics["avg_ndcg"]
            + 0.2 * metrics["avg_precision"]
        )

        record = {
            "precise_bm25": precise_bm25,
            "semantic_vector": semantic_vector,
            "avg_mrr": metrics["avg_mrr"],
            "avg_ndcg": metrics["avg_ndcg"],
            "avg_precision": metrics["avg_precision"],
            "combined_score": round(combined, 4),
            "queries": metrics["queries"],
            "details": metrics["details"],
        }
        results.append(record)

        if combined > best_score:
            best_score = combined
            best_weights = (precise_bm25, semantic_vector)

        logger.info(
            f"  -> MRR={metrics['avg_mrr']:.4f} NDCG={metrics['avg_ndcg']:.4f} "
            f"P={metrics['avg_precision']:.4f} combined={combined:.4f}"
        )

    results.sort(key=lambda x: x["combined_score"], reverse=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "llm_provider": settings.LLM_PROVIDER,
        "llm_base_url": settings.DEEPSEEK_BASE_URL if settings.LLM_PROVIDER == "deepseek" else None,
        "llm_model": settings.DEEPSEEK_MODEL if settings.LLM_PROVIDER == "deepseek" else None,
        "dataset": args.dataset,
        "dataset_size": len(dataset),
        "top_k": args.top_k,
        "candidates": candidates,
        "best_weights": {
            "precise_bm25": best_weights[0] if best_weights else None,
            "semantic_vector": best_weights[1] if best_weights else None,
            "combined_score": best_score,
        },
        "all_results": [
            {k: v for k, v in r.items() if k != "details"} for r in results
        ],
        "top_result_details": results[0]["details"] if results else [],
    }

    if args.output is None:
        out_dir = os.path.join(SCRIPT_DIR, "..", "eval")
        os.makedirs(out_dir, exist_ok=True)
        args.output = os.path.join(out_dir, "rrf_weight_tuning_llama_report.json")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 64)
    print(f"最优权重: precise_bm25={best_weights[0]}, semantic_vector={best_weights[1]}")
    print(f"最优综合分数: {best_score:.4f}")
    print(f"报告已保存: {args.output}")
    print("=" * 64)
    print("\n前 3 名权重组合:")
    for i, r in enumerate(results[:3], 1):
        print(
            f"  {i}. BM25={r['precise_bm25']:.1f} Vector={r['semantic_vector']:.1f} "
            f"combined={r['combined_score']:.4f} (MRR={r['avg_mrr']:.4f} "
            f"NDCG={r['avg_ndcg']:.4f} P={r['avg_precision']:.4f})"
        )


if __name__ == "__main__":
    main()
