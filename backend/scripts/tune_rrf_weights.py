# -*- coding: utf-8 -*-
"""
RRF 权重离线调优脚本
====================
对 query-type-aware RRF 的 BM25/Vector 权重做网格搜索，输出最优组合。

用法：
    python backend/scripts/tune_rrf_weights.py
    python backend/scripts/tune_rrf_weights.py --dataset backend/eval/golden_retrieval.jsonl
    python backend/scripts/tune_rrf_weights.py --candidates "1.0,1.2,1.5,2.0,3.0"

数据集格式（JSONL）：
    {"query": "查询 PO-20250101 状态", "query_type": "precise", "relevant_chunks": ["chunk_id_1", "chunk_id_2"]}
    {"query": "供应商准入流程是什么", "query_type": "semantic", "relevant_chunks": ["chunk_id_3"]}

如果没有提供数据集，脚本会使用内置的最小合成示例验证流程。
"""
import sys
import os
import json
import argparse
from itertools import product
from datetime import datetime

# 脚本所在目录 /backend/scripts
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

from app.config import get_settings
from app.core.retrieval_evaluator import RetrievalEvaluator

# 最小合成示例（仅用于无数据集时验证脚本可跑通）
DEFAULT_SYNTHETIC_DATASET = [
    {
        "query": "查询 PO-20250101 状态",
        "query_type": "precise",
        "relevant_chunks": ["po_20250101_1"],
    },
    {
        "query": "MAT-001 库存还有多少",
        "query_type": "precise",
        "relevant_chunks": ["mat_001_1"],
    },
    {
        "query": "供应商准入流程是什么",
        "query_type": "semantic",
        "relevant_chunks": ["supplier_onboarding_1"],
    },
    {
        "query": "来料检验不合格怎么处理",
        "query_type": "semantic",
        "relevant_chunks": ["iqc_reject_1"],
    },
    {
        "query": "安全库存标准是多少",
        "query_type": "semantic",
        "relevant_chunks": ["safety_stock_1"],
    },
]


def load_dataset(path: str | None):
    """加载验证集，支持 JSONL 或 JSON 列表。"""
    if path is None:
        print("[INFO] 未提供数据集，使用内置合成示例验证流程。")
        return DEFAULT_SYNTHETIC_DATASET

    if not os.path.exists(path):
        raise FileNotFoundError(f"数据集不存在: {path}")

    records = []
    with open(path, "r", encoding="utf-8") as f:
        if path.endswith(".jsonl"):
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        else:
            records = json.load(f)

    # 简单校验
    for i, r in enumerate(records):
        if "query" not in r or "relevant_chunks" not in r:
            raise ValueError(f"第 {i} 条记录缺少 query 或 relevant_chunks 字段")
        if "query_type" not in r:
            r["query_type"] = "default"
    return records


def set_weights(precise_bm25: float, semantic_vector: float, default: float = 1.0):
    """动态修改 settings 中的 RRF 权重。"""
    s = get_settings()
    s.RRF_BM25_WEIGHT_PRECISE = precise_bm25
    s.RRF_VECTOR_WEIGHT_SEMANTIC = semantic_vector
    s.RRF_BM25_WEIGHT_DEFAULT = default
    s.RRF_VECTOR_WEIGHT_DEFAULT = default


def evaluate_one(weights, dataset, top_k: int = 5):
    """对一组权重评估整个数据集，返回汇总指标。"""
    precise_bm25, semantic_vector = weights
    set_weights(precise_bm25, semantic_vector)

    # 注意：RAGEngine.search 需要 Milvus/BM25 等基础设施在线。
    # 为了离线调优，这里提供两种模式：
    # 1) 如果基础设施在线，直接调用 rag_engine.search
    # 2) 如果不在线，使用 mock 返回（由 --mock 控制）
    from app.core.rag_engine import rag_engine

    evaluator = RetrievalEvaluator()
    for item in dataset:
        result = rag_engine.search(
            item["query"],
            top_k=top_k,
            query_type=item.get("query_type", "default"),
        )
        retrieved_ids = [c.get("chunk_id", "") for c in result.get("results", [])]
        evaluator.evaluate_retrieval(
            query=item["query"],
            retrieved_chunk_ids=retrieved_ids,
            relevant_chunk_ids=item["relevant_chunks"],
            k_values=[1, 3, 5],
        )

    summary = evaluator.get_summary()
    return summary


def evaluate_mock(weights, dataset, top_k: int = 5):
    """Mock 模式：不依赖外部基础设施，验证指标计算与权重敏感性。

    模拟逻辑：
    - precise 查询：BM25 权重越高，相关 chunk 在结果中排名越靠前。
    - semantic 查询：向量权重越高，相关 chunk 在结果中排名越靠前。
    - default 查询：保持等权，相关 chunk 固定排在第 2 位。
    """
    precise_bm25, semantic_vector = weights
    evaluator = RetrievalEvaluator()

    def rank_for(qt: str) -> int:
        if qt == "precise":
            # 权重 1.0 -> rank 2, 1.5 -> rank 1, >=2.0 -> rank 0
            return max(0, 2 - int((precise_bm25 - 1.0) // 0.5))
        elif qt == "semantic":
            return max(0, 2 - int((semantic_vector - 1.0) // 0.5))
        else:
            return 1

    noise = ["noise_1", "noise_2", "noise_3", "noise_4", "noise_5"]
    for item in dataset:
        qt = item.get("query_type", "default")
        rel = item["relevant_chunks"][:1]
        r = rank_for(qt)
        retrieved = noise[:r] + rel + noise[r:]
        retrieved = retrieved[:top_k]

        evaluator.evaluate_retrieval(
            query=item["query"],
            retrieved_chunk_ids=retrieved,
            relevant_chunk_ids=item["relevant_chunks"],
            k_values=[1, 3, 5],
        )

    summary = evaluator.get_summary()
    summary["weights"] = {"precise_bm25": precise_bm25, "semantic_vector": semantic_vector}
    return summary


def main():
    parser = argparse.ArgumentParser(description="RRF 权重网格搜索")
    parser.add_argument("--dataset", type=str, default=None, help="验证集路径（JSONL/JSON）")
    parser.add_argument(
        "--candidates",
        type=str,
        default="1.0,1.2,1.5,2.0,3.0",
        help="权重候选值，逗号分隔",
    )
    parser.add_argument("--top-k", type=int, default=5, help="评估 Top-K")
    parser.add_argument("--mock", action="store_true", help="使用 mock 模式，不依赖后端基础设施")
    parser.add_argument("--output", type=str, default=None, help="结果输出 JSON 路径")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    candidates = [float(x.strip()) for x in args.candidates.split(",")]
    print(f"[INFO] 加载 {len(dataset)} 条验证数据，权重候选值: {candidates}")

    results = []
    best_score = -1.0
    best_weights = None

    for precise_bm25, semantic_vector in product(candidates, repeat=2):
        if args.mock:
            summary = evaluate_mock((precise_bm25, semantic_vector), dataset, args.top_k)
        else:
            summary = evaluate_one((precise_bm25, semantic_vector), dataset, args.top_k)

        score = summary.get("avg_retrieval_score", 0.0)
        results.append({
            "precise_bm25": precise_bm25,
            "semantic_vector": semantic_vector,
            "avg_retrieval_score": score,
            "avg_recall_at_5": summary.get("avg_recall_at_5", 0.0),
            "avg_ndcg_at_5": summary.get("avg_ndcg_at_5", 0.0),
            "avg_mrr": summary.get("avg_mrr", 0.0),
        })

        if score > best_score:
            best_score = score
            best_weights = (precise_bm25, semantic_vector)

        print(
            f"  BM25={precise_bm25:>4.1f} Vector={semantic_vector:>4.1f} "
            f"score={score:.4f} recall@5={summary.get('avg_recall_at_5', 0.0):.4f} "
            f"ndcg@5={summary.get('avg_ndcg_at_5', 0.0):.4f} mrr={summary.get('avg_mrr', 0.0):.4f}"
        )

    # 按分数排序
    results.sort(key=lambda x: x["avg_retrieval_score"], reverse=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "dataset_size": len(dataset),
        "candidates": candidates,
        "top_k": args.top_k,
        "mock": args.mock,
        "best_weights": {
            "precise_bm25": best_weights[0] if best_weights else None,
            "semantic_vector": best_weights[1] if best_weights else None,
            "avg_retrieval_score": best_score,
        },
        "all_results": results,
    }

    # 输出到文件
    if args.output is None:
        out_dir = os.path.join(SCRIPT_DIR, "..", "eval")
        os.makedirs(out_dir, exist_ok=True)
        args.output = os.path.join(out_dir, "rrf_weight_tuning_report.json")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"最优权重: precise_bm25={best_weights[0]}, semantic_vector={best_weights[1]}")
    print(f"最优 avg_retrieval_score: {best_score:.4f}")
    print(f"报告已保存: {args.output}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
