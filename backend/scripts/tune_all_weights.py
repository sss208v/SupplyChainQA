# -*- coding: utf-8 -*-
"""
RRF 权重全参数调优脚本（optuna + llama.cpp）
===============================================
使用 optuna TPE sampler 对 8 个参数做三阶段贝叶斯优化，
本地 llama.cpp 作为相关性裁判，零 API 费用。

三阶段：
  Stage 1 — RRF 主体权重 (5 params): precise_bm25, semantic_vector,
            default_bm25, default_vector, RRF_K
  Stage 2 — RRF_MIN_SCORE (1 param): 基于 Stage 1 best
  Stage 3 — GRAPH_FUSION_ALPHA/BETA (2 params): 基于 Stage 1+2 best

用法：
    pip install optuna
    python backend/scripts/tune_all_weights.py --max-queries 20 --n-trials 60
    python backend/scripts/tune_all_weights.py --disable-reranker --n-trials 80
"""

import sys, os, json, re, argparse, logging, math
from datetime import datetime
from itertools import product
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

import optuna
from optuna.samplers import TPESampler

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
    """使用 rag_engine 归一化逻辑检测查询类型。"""
    if not query:
        return "default"
    from app.core.rag.engine import RAGEngine
    normalized = RAGEngine._normalize_query_entities(query)
    has_semantic = bool(re.search(r'怎么|如何|什么|哪些|为什么|介绍|说明', query))
    has_precise = bool(re.search(
        r'[A-Z]{2,}-?\d{3,}|\d{4,}|MAT-\d+|PO-\d+|SUP-\d+', normalized
    ))
    if has_precise and not has_semantic:
        return "precise"
    if has_semantic and not has_precise:
        return "semantic"
    return "default"


def load_dataset(path: str, max_queries: Optional[int] = None):
    """加载评估数据集。"""
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
    precise_n = sum(1 for r in records if r["query_type"] == "precise")
    semantic_n = sum(1 for r in records if r["query_type"] == "semantic")
    default_n = sum(1 for r in records if r["query_type"] == "default")
    logger.info(
        f"加载 {len(records)} 条评估数据 "
        f"(precise={precise_n}, semantic={semantic_n}, default={default_n})"
    )
    return records


class LlamaRelevanceJudge:
    """使用本地 llama.cpp 评估上下文相关性。"""

    def __init__(self, base_url: str = settings.LOCAL_LLM_BASE_URL,
                 model: str = settings.LOCAL_LLM_MODEL, temperature: float = 0.0):
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self._cache: dict[str, float] = {}
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                base_url=self.base_url,
                api_key="not-needed",
                model=self.model,
                temperature=self.temperature,
            )
        return self._llm

    def _cache_key(self, query: str, context: str) -> str:
        return f"{hash(query) & 0xFFFFFFFF}_{hash(context) & 0xFFFFFFFF}"

    def judge(self, query: str, context: str) -> float:
        key = self._cache_key(query, context)
        if key in self._cache:
            return self._cache[key]
        prompt = JUDGE_PROMPT.format(query=query, context=context[:2000])
        try:
            llm = self._get_llm()
            from langchain_core.messages import SystemMessage, HumanMessage
            resp = llm.invoke([
                SystemMessage(content="你是检索相关性评估专家。"),
                HumanMessage(content=prompt),
            ])
            text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
            match = re.search(r"\b(\d{1,2})(\.\d+)?\b", text)
            score = float(match.group(1)) / 10.0 if match else 0.0
        except Exception as e:
            logger.error(f"LLM 判断失败: {e}")
            score = 0.0
        score = max(0.0, min(1.0, score))
        self._cache[key] = score
        return score


def compute_dcg(relevances: list[float]) -> float:
    return sum((2 ** rel - 1) / math.log2(i + 2) for i, rel in enumerate(relevances))


def evaluate_current_weights(
    dataset: list[dict],
    judge: LlamaRelevanceJudge,
    top_k: int = 5,
) -> dict:
    """使用当前 settings 中的权重评估整个数据集。"""
    rag_engine._query_cache.clear()

    mrr_sum, ndcg_sum, precision_sum = 0.0, 0.0, 0.0
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

        mrr = 0.0
        for i, rel in enumerate(relevances):
            if rel >= 0.5:
                mrr = 1.0 / (i + 1)
                break

        ideal = sorted(relevances, reverse=True)
        dcg_val = compute_dcg(relevances)
        idcg_val = compute_dcg(ideal)
        ndcg_val = dcg_val / idcg_val if idcg_val > 0 else 0.0

        precision_val = sum(1 for r in relevances if r >= 0.5) / len(relevances)

        mrr_sum += mrr
        ndcg_sum += ndcg_val
        precision_sum += precision_val
        query_count += 1
        details.append({
            "query": query, "query_type": qt,
            "mrr": round(mrr, 4), "ndcg": round(ndcg_val, 4),
            "precision": round(precision_val, 4),
            "relevances": [round(r, 2) for r in relevances],
        })

    if query_count == 0:
        return {"avg_mrr": 0, "avg_ndcg": 0, "avg_precision": 0, "queries": 0, "details": details}

    return {
        "avg_mrr": round(mrr_sum / query_count, 4),
        "avg_ndcg": round(ndcg_sum / query_count, 4),
        "avg_precision": round(precision_sum / query_count, 4),
        "queries": query_count,
        "details": details,
    }


# ============================================================
# Stage 1: RRF 类型权重 + RRF_K (5 params)
# ============================================================

def stage1_objective(trial, dataset, judge, disable_reranker):
    """optuna objective: Stage 1 — RRF weights + RRF_K."""
    precise_bm25 = trial.suggest_float("precise_bm25", 0.5, 3.0, step=0.25)
    semantic_vector = trial.suggest_float("semantic_vector", 0.5, 3.0, step=0.25)
    default_bm25 = trial.suggest_float("default_bm25", 0.5, 2.0, step=0.25)
    default_vector = trial.suggest_float("default_vector", 0.5, 2.0, step=0.25)
    rrf_k = trial.suggest_int("rrf_k", 30, 120, step=10)

    # Apply to settings
    settings.RRF_BM25_WEIGHT_PRECISE = precise_bm25
    settings.RRF_VECTOR_WEIGHT_SEMANTIC = semantic_vector
    settings.RRF_BM25_WEIGHT_DEFAULT = default_bm25
    settings.RRF_VECTOR_WEIGHT_DEFAULT = default_vector
    settings.RRF_K = rrf_k
    if disable_reranker:
        settings.RERANKER_ENABLED = False

    metrics = evaluate_current_weights(dataset, judge)
    combined = 0.4 * metrics["avg_mrr"] + 0.4 * metrics["avg_ndcg"] + 0.2 * metrics["avg_precision"]

    trial.set_user_attr("mrr", metrics["avg_mrr"])
    trial.set_user_attr("ndcg", metrics["avg_ndcg"])
    trial.set_user_attr("precision", metrics["avg_precision"])
    trial.set_user_attr("queries", metrics["queries"])

    return combined


# ============================================================
# Stage 2: RRF_MIN_SCORE (1 param)
# ============================================================

def stage2_objective(trial, dataset, judge, best_stage1):
    """optuna objective: Stage 2 — RRF_MIN_SCORE（基于 Stage 1 best）。"""
    min_score = trial.suggest_float("rrf_min_score", 0.001, 0.05, log=True)

    # Restore Stage 1 best
    settings.RRF_BM25_WEIGHT_PRECISE = best_stage1["precise_bm25"]
    settings.RRF_VECTOR_WEIGHT_SEMANTIC = best_stage1["semantic_vector"]
    settings.RRF_BM25_WEIGHT_DEFAULT = best_stage1["default_bm25"]
    settings.RRF_VECTOR_WEIGHT_DEFAULT = best_stage1["default_vector"]
    settings.RRF_K = best_stage1["rrf_k"]
    settings.RRF_MIN_SCORE = min_score

    metrics = evaluate_current_weights(dataset, judge)
    combined = 0.4 * metrics["avg_mrr"] + 0.4 * metrics["avg_ndcg"] + 0.2 * metrics["avg_precision"]

    trial.set_user_attr("mrr", metrics["avg_mrr"])
    trial.set_user_attr("ndcg", metrics["avg_ndcg"])
    trial.set_user_attr("precision", metrics["avg_precision"])

    return combined


# ============================================================
# Stage 3: GRAPH_FUSION_ALPHA/BETA (2 params)
# ============================================================

def stage3_objective(trial, dataset, judge, best_stage1, best_stage2):
    """optuna objective: Stage 3 — Graph 融合权重（基于 Stage 1+2 best）。"""
    alpha = trial.suggest_float("graph_alpha", 0.3, 0.9, step=0.05)
    beta = trial.suggest_float("graph_beta", 0.1, 0.7, step=0.05)

    # Restore Stage 1+2 best
    settings.RRF_BM25_WEIGHT_PRECISE = best_stage1["precise_bm25"]
    settings.RRF_VECTOR_WEIGHT_SEMANTIC = best_stage1["semantic_vector"]
    settings.RRF_BM25_WEIGHT_DEFAULT = best_stage1["default_bm25"]
    settings.RRF_VECTOR_WEIGHT_DEFAULT = best_stage1["default_vector"]
    settings.RRF_K = best_stage1["rrf_k"]
    settings.RRF_MIN_SCORE = best_stage2["rrf_min_score"]
    settings.GRAPH_FUSION_ALPHA = alpha
    settings.GRAPH_FUSION_BETA = beta

    metrics = evaluate_current_weights(dataset, judge)
    combined = 0.4 * metrics["avg_mrr"] + 0.4 * metrics["avg_ndcg"] + 0.2 * metrics["avg_precision"]

    trial.set_user_attr("mrr", metrics["avg_mrr"])
    trial.set_user_attr("ndcg", metrics["avg_ndcg"])
    trial.set_user_attr("precision", metrics["avg_precision"])

    return combined


# ============================================================
# Report Generation
# ============================================================

def format_ablation_table(stage_name: str, trials: list, param_names: list[str]) -> str:
    """生成 ablation 对比表（Markdown）。"""
    if not trials:
        return f"## {stage_name}\n(无数据)\n"

    lines = [f"## {stage_name}", ""]
    header = "| " + " | ".join(param_names + ["MRR", "NDCG", "Precision", "Combined"]) + " |"
    sep = "|" + "|".join(["---"] * (len(param_names) + 4)) + "|"
    lines.append(header)
    lines.append(sep)

    for t in sorted(trials, key=lambda x: x["combined"], reverse=True)[:15]:
        vals = [f"{t.get(p, '?')}" for p in param_names]
        vals += [
            f"{t.get('mrr', 0):.4f}",
            f"{t.get('ndcg', 0):.4f}",
            f"{t.get('precision', 0):.4f}",
            f"{t.get('combined', 0):.4f}",
        ]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="全参数 RRF 权重调优 (optuna + llama.cpp)")
    parser.add_argument("--dataset", type=str, default=DEFAULT_DATASET)
    parser.add_argument("--max-queries", type=int, default=20)
    parser.add_argument("--n-trials", type=int, default=60, help="Stage 1 optuna trials")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--llama-url", type=str, default=settings.LOCAL_LLM_BASE_URL)
    parser.add_argument("--disable-reranker", action="store_true",
                        help="关闭重排序，单独观察 RRF 融合效果")
    parser.add_argument("--rebuild-bm25", action="store_true")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--skip-stage2", action="store_true")
    parser.add_argument("--skip-stage3", action="store_true")
    args = parser.parse_args()

    if args.output_dir is None:
        args.output_dir = os.path.join(SCRIPT_DIR, "..", "eval")

    # 重建 BM25
    if args.rebuild_bm25 or getattr(rag_engine.bm25, '_bm25', None) is None:
        logger.info("检查/重建 BM25 索引...")
        try:
            total = rebuild_bm25_from_milvus(rag_engine, milvus_manager)
            logger.info(f"BM25 chunk 数: {total}")
        except Exception as e:
            logger.error(f"BM25 重建失败: {e}")

    dataset = load_dataset(args.dataset, args.max_queries)
    judge = LlamaRelevanceJudge(base_url=args.llama_url)

    logger.info(f"LLM 裁判: {args.llama_url}, Top-K: {args.top_k}")
    logger.info(f"数据集: {len(dataset)} 条, Optuna trials: {args.n_trials}")
    if args.disable_reranker:
        settings.RERANKER_ENABLED = False
        logger.info("已关闭 Reranker")

    all_stage_results = []
    final_best = {}

    # ======== Stage 1 ========
    logger.info("=" * 60)
    logger.info("Stage 1: RRF 类型权重 + RRF_K (5 params, TPE sampler)")
    logger.info("=" * 60)

    study1 = optuna.create_study(
        direction="maximize",
        sampler=TPESampler(seed=42),
        study_name="rrf_stage1_weights_k",
    )
    study1.optimize(
        lambda trial: stage1_objective(trial, dataset, judge, args.disable_reranker),
        n_trials=args.n_trials,
        show_progress_bar=True,
    )

    best1 = {
        "precise_bm25": study1.best_params["precise_bm25"],
        "semantic_vector": study1.best_params["semantic_vector"],
        "default_bm25": study1.best_params["default_bm25"],
        "default_vector": study1.best_params["default_vector"],
        "rrf_k": study1.best_params["rrf_k"],
        "combined": study1.best_value,
        "mrr": study1.best_trial.user_attrs.get("mrr", 0),
        "ndcg": study1.best_trial.user_attrs.get("ndcg", 0),
        "precision": study1.best_trial.user_attrs.get("precision", 0),
    }
    logger.info(f"Stage 1 Best: {json.dumps(best1, ensure_ascii=False, indent=2)}")

    # Stage 1 ablation table
    s1_trials = []
    for t in study1.trials:
        if t.state == optuna.trial.TrialState.COMPLETE:
            s1_trials.append({
                "precise_bm25": t.params.get("precise_bm25"),
                "semantic_vector": t.params.get("semantic_vector"),
                "default_bm25": t.params.get("default_bm25"),
                "default_vector": t.params.get("default_vector"),
                "rrf_k": t.params.get("rrf_k"),
                "mrr": t.user_attrs.get("mrr", 0),
                "ndcg": t.user_attrs.get("ndcg", 0),
                "precision": t.user_attrs.get("precision", 0),
                "combined": t.value,
            })
    all_stage_results.append(format_ablation_table(
        "Stage 1: RRF Weights + RRF_K",
        s1_trials,
        ["precise_bm25", "semantic_vector", "default_bm25", "default_vector", "rrf_k"],
    ))

    # ======== Stage 2 ========
    if not args.skip_stage2:
        logger.info("=" * 60)
        logger.info("Stage 2: RRF_MIN_SCORE (1 param)")
        logger.info("=" * 60)

        study2 = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(seed=42),
            study_name="rrf_stage2_min_score",
        )
        study2.optimize(
            lambda trial: stage2_objective(trial, dataset, judge, best1),
            n_trials=15,
        )

        best2 = {
            "rrf_min_score": study2.best_params["rrf_min_score"],
            "combined": study2.best_value,
            "mrr": study2.best_trial.user_attrs.get("mrr", 0),
            "ndcg": study2.best_trial.user_attrs.get("ndcg", 0),
            "precision": study2.best_trial.user_attrs.get("precision", 0),
        }
        best2.update(best1)
        logger.info(f"Stage 2 Best: {json.dumps(best2, ensure_ascii=False, indent=2)}")

        s2_trials = []
        for t in study2.trials:
            if t.state == optuna.trial.TrialState.COMPLETE:
                s2_trials.append({
                    "rrf_min_score": t.params.get("rrf_min_score"),
                    "mrr": t.user_attrs.get("mrr", 0),
                    "ndcg": t.user_attrs.get("ndcg", 0),
                    "precision": t.user_attrs.get("precision", 0),
                    "combined": t.value,
                })
        all_stage_results.append(format_ablation_table(
            "Stage 2: RRF_MIN_SCORE",
            s2_trials,
            ["rrf_min_score"],
        ))
    else:
        best2 = {**best1, "rrf_min_score": settings.RRF_MIN_SCORE}

    # ======== Stage 3 ========
    if not args.skip_stage3:
        logger.info("=" * 60)
        logger.info("Stage 3: GRAPH_FUSION_ALPHA/BETA (2 params)")
        logger.info("=" * 60)

        study3 = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(seed=42),
            study_name="rrf_stage3_graph_fusion",
        )
        study3.optimize(
            lambda trial: stage3_objective(trial, dataset, judge, best1, best2),
            n_trials=30,
        )

        best3 = {
            "graph_alpha": study3.best_params["graph_alpha"],
            "graph_beta": study3.best_params["graph_beta"],
            "combined": study3.best_value,
            "mrr": study3.best_trial.user_attrs.get("mrr", 0),
            "ndcg": study3.best_trial.user_attrs.get("ndcg", 0),
            "precision": study3.best_trial.user_attrs.get("precision", 0),
        }
        best3.update(best2)
        logger.info(f"Stage 3 Best: {json.dumps(best3, ensure_ascii=False, indent=2)}")

        s3_trials = []
        for t in study3.trials:
            if t.state == optuna.trial.TrialState.COMPLETE:
                s3_trials.append({
                    "graph_alpha": t.params.get("graph_alpha"),
                    "graph_beta": t.params.get("graph_beta"),
                    "mrr": t.user_attrs.get("mrr", 0),
                    "ndcg": t.user_attrs.get("ndcg", 0),
                    "precision": t.user_attrs.get("precision", 0),
                    "combined": t.value,
                })
        all_stage_results.append(format_ablation_table(
            "Stage 3: GRAPH_FUSION_ALPHA/BETA",
            s3_trials,
            ["graph_alpha", "graph_beta"],
        ))

        final_best = best3
    else:
        final_best = best2

    # ======== Report ========
    report = {
        "timestamp": datetime.now().isoformat(),
        "dataset": args.dataset,
        "dataset_size": len(dataset),
        "top_k": args.top_k,
        "n_trials_stage1": args.n_trials,
        "reranker_disabled": args.disable_reranker,
        "llama_url": args.llama_url,
        "best_params": final_best,
        "ablation_tables": "\n\n".join(all_stage_results),
    }

    report_path = os.path.join(args.output_dir, "rrf_full_tuning_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md_path = os.path.join(args.output_dir, "rrf_full_tuning_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# RRF 全参数调优报告\n\n")
        f.write(f"**时间**: {report['timestamp']}\n")
        f.write(f"**数据集**: {len(dataset)} 条查询\n")
        f.write(f"**Reranker**: {'关闭' if args.disable_reranker else '启用'}\n\n")
        f.write(f"## 最优参数\n\n```json\n{json.dumps(final_best, ensure_ascii=False, indent=2)}\n```\n\n")
        f.write(report["ablation_tables"])

    print("\n" + "=" * 64)
    print("全参数调优完成")
    print(f"最优参数: {json.dumps(final_best, ensure_ascii=False, indent=2)}")
    print(f"JSON 报告: {report_path}")
    print(f"Markdown 报告: {md_path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
