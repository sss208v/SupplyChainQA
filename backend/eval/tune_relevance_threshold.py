# -*- coding: utf-8 -*-
"""LLM 相关性过滤阈值调优实验 —— 扫 LLM_RELEVANCE_THRESHOLD，用【官方 RAGAS】度量 CP/CR 权衡。

【重要澄清】这不是论文级 Self-RAG，而是"借鉴 Self-RAG 思想的 LLM-as-Judge 相关性过滤"
（app/core/llm_relevance.py：检索后一次 LLM 调用给所有 chunk 打分 0-1 + 阈值过滤）。
阈值现为 0.15（极松，只滤掉 0-0.2 的"几乎不相关"，留下弱/部分相关 → CP 上不去）。

【控制变量】强制 full 策略（use_self_rag=True + reranker），让相关性过滤真正生效；仅阈值变化。
【生效原理】过滤器是单例且在 __init__ 缓存阈值 → 每档必须直接改单例的 RELEVANCE_THRESHOLD，
           光改 settings 不生效（触发条件另见 rag.py L295-299：还需 ≥4 个候选块）。
【复用】collect_data（全链路生成）+ run_ragas_eval（官方 ragas 0.4.3 LLM-as-Judge）。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_relevance_threshold.py --values 0.15,0.3,0.5,0.7 --limit 56
  venv\\Scripts\\python.exe eval\\tune_relevance_threshold.py --values 0.15,0.5 --limit 3   # 冒烟
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.run_comprehensive_ragas import collect_data, run_ragas_eval, _questions, LLAMA_MODEL
from app.config import get_settings
from app.core import query_analyzer as _qa
from app.core.rag_engine import rag_engine
from app.core.llm_relevance import get_self_rag

settings = get_settings()
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _force_full():
    """强制所有查询走 full 策略（use_self_rag=True + reranker），让 LLM 相关性过滤触发。"""
    base = dict(_qa.STRATEGIES["full"])
    base["use_self_rag"] = True
    base["use_reranker"] = True
    _qa.query_analyzer.get_strategy_config = lambda strategy: dict(base)


def _set_threshold(t: float):
    """设置阈值：过滤器单例在 __init__ 缓存了阈值，必须直接改单例属性才生效。"""
    settings.LLM_RELEVANCE_THRESHOLD = t
    get_self_rag().RELEVANCE_THRESHOLD = t


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    thresholds = [float(x) for x in args.values.split(",")]
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit)
    _force_full()

    rows = []
    for t in thresholds:
        print("=" * 60)
        print(f"[threshold={t}] full 策略 + LLM_RELEVANCE_THRESHOLD={t}, 生成 {len(questions)} 题 ...")
        _set_threshold(t)
        if hasattr(rag_engine, "_query_cache"):
            try:
                rag_engine._query_cache.clear()
            except Exception:
                pass

        t0 = time.time()
        eval_data = await collect_data(questions, gen)
        gen_sec = time.time() - t0
        avg_ctx = sum(len(d["retrieved_contexts"]) for d in eval_data) / len(eval_data)

        ragas = run_ragas_eval(eval_data, judge)
        vals = [v for v in ragas.values() if v is not None]
        rows.append({
            "threshold": t,
            "avg_contexts": round(avg_ctx, 2),
            "gen_seconds": round(gen_sec, 1),
            "faithfulness": ragas.get("faithfulness"),
            "answer_relevancy": ragas.get("answer_relevancy"),
            "context_precision": ragas.get("context_precision"),
            "context_recall": ragas.get("context_recall"),
            "overall": round(sum(vals) / len(vals), 4) if vals else None,
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "gen_model": gen, "n": len(questions), "sweep": rows}, f,
                  ensure_ascii=False, indent=2)

    print("\n" + "=" * 84)
    print(f"LLM 相关性过滤阈值调优对照（官方 RAGAS, judge={judge}, n={len(questions)}）")
    print("%-10s %-9s %-7s %-7s %-7s %-7s %-8s %-8s" % (
        "threshold", "avg_ctx", "Faith", "AR", "CP", "CR", "Overall", "gen(s)"))
    for r in rows:
        print("%-10s %-9.2f %-7s %-7s %-7s %-7s %-8s %-8.1f" % (
            str(r["threshold"]), r["avg_contexts"], _fmt(r["faithfulness"]), _fmt(r["answer_relevancy"]),
            _fmt(r["context_precision"]), _fmt(r["context_recall"]), _fmt(r["overall"]), r["gen_seconds"]))
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="LLM 相关性过滤阈值调优（官方 RAGAS）")
    ap.add_argument("--values", default="0.15,0.3,0.5,0.7", help="逗号分隔的阈值候选")
    ap.add_argument("--limit", type=int, default=56, help="题量（0=全部）")
    ap.add_argument("--gen-model", default=None)
    ap.add_argument("--judge-model", default=None, help="默认取 .env RAGAS_JUDGE_MODEL")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "relevance_threshold_sweep_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
