# -*- coding: utf-8 -*-
"""LLM 相关性过滤 开/关 A/B —— 生产路由下对比"过滤器开(0.15)"vs"关闭"，官方 RAGAS。

回答"去掉这个（非论文级、借鉴 Self-RAG 思想的）LLM 相关性过滤器会不会更好"。
生产路由（不强制策略，真实系统行为），仅切换 settings.LLM_RELEVANCE_ENABLED。

【生效原理】rag.py L296 每次查询实时读 settings.LLM_RELEVANCE_ENABLED，直接 mutate 即生效
（过滤器仅在 full 策略 + ENABLED + ≥4 块时触发，故只影响复杂查询）。
【复用】collect_data（全链路生成）+ run_ragas_eval（官方 ragas 0.4.3）。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_relevance_onoff.py --limit 20
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
from app.core.rag_engine import rag_engine

settings = get_settings()
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    gen = os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit)

    rows = []
    for enabled in [True, False]:
        label = "ON(0.15)" if enabled else "OFF"
        print("=" * 60)
        print(f"[filter={label}] LLM_RELEVANCE_ENABLED={enabled}, 生产路由, {len(questions)} 题 ...")
        settings.LLM_RELEVANCE_ENABLED = enabled
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
            "filter": label,
            "avg_contexts": round(avg_ctx, 2),
            "gen_seconds": round(gen_sec, 1),
            "faithfulness": ragas.get("faithfulness"),
            "answer_relevancy": ragas.get("answer_relevancy"),
            "context_precision": ragas.get("context_precision"),
            "context_recall": ragas.get("context_recall"),
            "overall": round(sum(vals) / len(vals), 4) if vals else None,
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "n": len(questions), "sweep": rows}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 82)
    print(f"LLM 相关性过滤 开/关 A/B（官方 RAGAS, judge={judge}, n={len(questions)}, 生产路由）")
    print("%-10s %-9s %-7s %-7s %-7s %-7s %-8s %-8s" % (
        "filter", "avg_ctx", "Faith", "AR", "CP", "CR", "Overall", "gen(s)"))
    for r in rows:
        print("%-10s %-9.2f %-7s %-7s %-7s %-7s %-8s %-8.1f" % (
            r["filter"], r["avg_contexts"], _fmt(r["faithfulness"]), _fmt(r["answer_relevancy"]),
            _fmt(r["context_precision"]), _fmt(r["context_recall"]), _fmt(r["overall"]), r["gen_seconds"]))
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="LLM 相关性过滤 开/关 A/B（官方 RAGAS）")
    ap.add_argument("--limit", type=int, default=20, help="题量（0=全部）")
    ap.add_argument("--judge-model", default=None, help="默认取 .env RAGAS_JUDGE_MODEL")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "relevance_onoff_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
