"""
tune_router_thresholds.py — 意图路由/语义缓存阈值离线调参脚本

用真实 embedding 引擎对标注数据集扫描阈值，输出各阈值下的
准确率/覆盖率/误命中率报告，替代拍脑袋配置（0.65 / 0.92）。

用法（需要 embedding 服务可用）：
    cd backend
    venv\\Scripts\\python.exe scripts\\tune_router_thresholds.py
    venv\\Scripts\\python.exe scripts\\tune_router_thresholds.py --dataset my_labels.jsonl

外部数据集格式（JSONL，每行一条）：
    {"query": "帮我查一下MAT-001库存", "intent": "tool_call"}

报告输出：backend/eval/router_threshold_report.json
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# 内置标注集（query, intent）— eval 数据集为 RAG 问答对，无意图标签，
# 此处内置覆盖 5 类意图的种子标注；生产可用 --dataset 追加路由日志回流样本
# ---------------------------------------------------------------------------
LABELED_QUERIES: list[tuple[str, str]] = [
    # rag_answer
    ("供应商准入要提交哪些资质文件", "rag_answer"),
    ("安全库存怎么计算", "rag_answer"),
    ("呆滞物料的判定标准", "rag_answer"),
    ("IQC来料检验的抽检比例", "rag_answer"),
    ("采购审批权限是怎么分级的", "rag_answer"),
    ("ABC分类法的原理", "rag_answer"),
    ("盘点差异超过多少要专项调查", "rag_answer"),
    ("供应商绩效评估的权重", "rag_answer"),
    ("不合格品让步接收的条件", "rag_answer"),
    ("物料编码第三位代表什么", "rag_answer"),
    # tool_call
    ("查一下MAT-005还剩多少库存", "tool_call"),
    ("MAT-010的库存够不够用", "tool_call"),
    ("PO-20250301这单到货了没有", "tool_call"),
    ("帮我看下采购单的状态", "tool_call"),
    ("给我建一个补货工单", "tool_call"),
    ("现在几点了", "tool_call"),
    ("帮我查下物料库存情况", "tool_call"),
    # greeting
    ("你好呀", "greeting"),
    ("在吗", "greeting"),
    ("谢谢你", "greeting"),
    # goal
    ("帮我评估一下MAT-001缺货的风险", "goal"),
    ("分析下供应商延迟交货对生产的影响", "goal"),
    ("这批料不够了该怎么应对", "goal"),
    # graph_query
    ("MAT-001缺货会影响哪些物料", "graph_query"),
    ("PO-100延迟影响的订单有哪些", "graph_query"),
    ("追溯TK-200关联的工单", "graph_query"),
]

# 语义缓存调参：同义改写对（应命中）+ 不同问题对（不应命中）
CACHE_POSITIVE_PAIRS = [
    ("查一下MAT-001的库存", "帮我查MAT-001库存多少"),
    ("供应商准入需要什么资质", "供应商准入要哪些资质文件"),
    ("安全库存怎么计算", "安全库存的计算方法是什么"),
    ("呆滞料怎么处理", "呆滞物料的处理办法"),
]
CACHE_NEGATIVE_PAIRS = [
    ("查一下MAT-001的库存", "查一下MAT-002的订单状态"),
    ("供应商准入需要什么资质", "供应商淘汰的流程是什么"),
    ("安全库存怎么计算", "盘点差异怎么处理"),
    ("呆滞料怎么处理", "紧急采购怎么走审批"),
]

SEMANTIC_THRESHOLDS = [round(0.50 + i * 0.05, 2) for i in range(8)]   # 0.50 ~ 0.85
CACHE_THRESHOLDS = [round(0.80 + i * 0.02, 2) for i in range(10)]     # 0.80 ~ 0.98


def _load_external_dataset(path: str) -> list[tuple[str, str]]:
    """加载外部 JSONL 标注集（query/intent 字段）"""
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("query") and item.get("intent"):
                samples.append((item["query"], item["intent"]))
    return samples


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


def tune_semantic_router(embedding_engine, samples: list[tuple[str, str]]) -> dict:
    """扫描语义路由阈值：对每个阈值统计覆盖率（命中语义层比例）与命中内准确率"""
    from app.core.intent_routes import get_intent_routes

    cfg = get_intent_routes()
    route_embeddings: dict[str, list[np.ndarray]] = {}
    for intent, route in cfg.semantic_routes.items():
        route_embeddings[intent] = [
            np.array(embedding_engine.embed_query(u)) for u in route["utterances"]
        ]

    # 预计算每个样本的 per-intent 最高分
    scored = []
    for query, gold in samples:
        q = np.array(embedding_engine.embed_query(query))
        intent_scores = {
            intent: max(_cosine(q, v) for v in vecs)
            for intent, vecs in route_embeddings.items()
        }
        ranked = sorted(intent_scores.items(), key=lambda kv: kv[1], reverse=True)
        margin = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else 1.0
        scored.append({
            "query": query, "gold": gold,
            "pred": ranked[0][0], "score": ranked[0][1], "margin": margin,
        })

    rows = []
    for th in SEMANTIC_THRESHOLDS:
        routed = [s for s in scored if s["score"] >= th]
        correct = [s for s in routed if s["pred"] == s["gold"]]
        rows.append({
            "threshold": th,
            "coverage": round(len(routed) / len(scored), 3),
            "accuracy_when_routed": round(len(correct) / len(routed), 3) if routed else None,
            "routed": len(routed),
            "correct": len(correct),
        })

    mistakes = [s for s in scored if s["pred"] != s["gold"]]
    return {"sweep": rows, "misrouted_samples": mistakes}


def tune_semantic_cache(embedding_engine) -> dict:
    """扫描语义缓存阈值：同义对召回率 vs 异义对误命中率"""
    pos_scores, neg_scores = [], []
    for a, b in CACHE_POSITIVE_PAIRS:
        va = np.array(embedding_engine.embed_query(a))
        vb = np.array(embedding_engine.embed_query(b))
        pos_scores.append(_cosine(va, vb))
    for a, b in CACHE_NEGATIVE_PAIRS:
        va = np.array(embedding_engine.embed_query(a))
        vb = np.array(embedding_engine.embed_query(b))
        neg_scores.append(_cosine(va, vb))

    rows = []
    for th in CACHE_THRESHOLDS:
        recall = sum(1 for s in pos_scores if s >= th) / len(pos_scores)
        false_hit = sum(1 for s in neg_scores if s >= th) / len(neg_scores)
        rows.append({
            "threshold": th,
            "paraphrase_recall": round(recall, 3),
            "false_hit_rate": round(false_hit, 3),
        })

    return {
        "sweep": rows,
        "positive_scores": [round(s, 4) for s in pos_scores],
        "negative_scores": [round(s, 4) for s in neg_scores],
    }


def main():
    parser = argparse.ArgumentParser(description="意图路由/语义缓存阈值离线调参")
    parser.add_argument("--dataset", help="外部 JSONL 标注集路径（追加到内置标注集）")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "eval", "router_threshold_report.json"),
        help="报告输出路径",
    )
    args = parser.parse_args()

    samples = list(LABELED_QUERIES)
    if args.dataset:
        extra = _load_external_dataset(args.dataset)
        print(f"外部标注集追加 {len(extra)} 条")
        samples.extend(extra)

    from app.core.rag_engine import rag_engine
    embedding_engine = rag_engine.embedding

    print(f"标注样本 {len(samples)} 条，开始扫描语义路由阈值 {SEMANTIC_THRESHOLDS} ...")
    router_report = tune_semantic_router(embedding_engine, samples)
    print(f"{'threshold':>10} {'coverage':>10} {'accuracy':>10}")
    for row in router_report["sweep"]:
        acc = row["accuracy_when_routed"]
        print(f"{row['threshold']:>10} {row['coverage']:>10} {acc if acc is not None else '-':>10}")

    print(f"\n开始扫描语义缓存阈值 {CACHE_THRESHOLDS} ...")
    cache_report = tune_semantic_cache(embedding_engine)
    print(f"{'threshold':>10} {'recall':>10} {'false_hit':>10}")
    for row in cache_report["sweep"]:
        print(f"{row['threshold']:>10} {row['paraphrase_recall']:>10} {row['false_hit_rate']:>10}")

    report = {
        "semantic_router": router_report,
        "semantic_cache": cache_report,
        "sample_count": len(samples),
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {args.output}")
    print("建议：选择 accuracy_when_routed >= 0.95 前提下 coverage 最高的路由阈值；")
    print("      选择 false_hit_rate = 0 前提下 paraphrase_recall 最高的缓存阈值。")


if __name__ == "__main__":
    main()
