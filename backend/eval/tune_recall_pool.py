# -*- coding: utf-8 -*-
"""三路召回候选池调优实验 —— 扫描 VECTOR_TOP_K/BM25_TOP_K（候选池），用【官方 RAGAS】+ 延迟度量。

候选池是"召回/延迟"权衡（不像 RERANK_TOP_K 是精度/召回）：
- 调大：只增 reranker 延迟，质量在 50-100 后饱和；
- 调小：可能漏召回（ContextRecall 降）。
本实验找"ContextRecall/Overall 不掉、延迟最低"的最小候选池 pool*。

【控制变量】强制 standard 策略（固定 RERANK_TOP_K、reranker 开），仅候选池变化。
【复用】collect_data（全链路生成）+ run_ragas_eval（官方 ragas 0.4.3 LLM-as-Judge）。
【生效原理】rag/engine.py 在 search 时实时读 settings.VECTOR_TOP_K/BM25_TOP_K，直接 mutate 即生效。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_recall_pool.py --values 30,50,100,200 --limit 20
  venv\\Scripts\\python.exe eval\\tune_recall_pool.py --values 50,100 --limit 3   # 冒烟
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

settings = get_settings()
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _force_standard():
    """强制所有查询走 standard 策略（reranker 开、固定 top_k），隔离候选池变量。"""
    base = dict(_qa.STRATEGIES["standard"])
    base["use_reranker"] = True
    _qa.query_analyzer.get_strategy_config = lambda strategy: dict(base)


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    pools = [int(x) for x in args.values.split(",")]
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit)
    _force_standard()

    rows = []
    for pool in pools:
        print("=" * 60)
        print(f"[pool={pool}] VECTOR_TOP_K=BM25_TOP_K={pool}, standard 策略, 生成 {len(questions)} 题 ...")
        settings.VECTOR_TOP_K = pool
        settings.BM25_TOP_K = pool
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
            "pool": pool,
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

    print("\n" + "=" * 82)
    print(f"候选池调优对照（官方 RAGAS, judge={judge}, n={len(questions)}）")
    print("%-6s %-9s %-7s %-7s %-7s %-7s %-8s %-8s" % (
        "pool", "avg_ctx", "Faith", "AR", "CP", "CR", "Overall", "gen(s)"))
    for r in rows:
        print("%-6d %-9.2f %-7s %-7s %-7s %-7s %-8s %-8.1f" % (
            r["pool"], r["avg_contexts"], _fmt(r["faithfulness"]), _fmt(r["answer_relevancy"]),
            _fmt(r["context_precision"]), _fmt(r["context_recall"]), _fmt(r["overall"]), r["gen_seconds"]))
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="三路召回候选池调优（官方 RAGAS）")
    ap.add_argument("--values", default="30,50,100,200", help="逗号分隔的候选池大小(VECTOR_TOP_K=BM25_TOP_K)")
    ap.add_argument("--limit", type=int, default=20, help="题量（0=全部）")
    ap.add_argument("--gen-model", default=None)
    ap.add_argument("--judge-model", default=None, help="默认取 .env RAGAS_JUDGE_MODEL")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "recall_pool_sweep_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
