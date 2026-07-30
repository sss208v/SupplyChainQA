# -*- coding: utf-8 -*-
"""精度过滤 A/B —— rerank 分数截断 + 降多查询扇出，官方 RAGAS + avg_ctx。

对比 baseline（现状）vs 两项新杠杆的组合，验证是否真能"压 CP 分母、提精度"。
生产路由（不强制策略，真实系统行为），逐档切 settings.RERANK_SCORE_THRESHOLD / MAX_SUB_QUERIES。

【生效原理】engine.search 每次实时读 settings.RERANK_SCORE_THRESHOLD（L297）；
           _generate_sub_queries 实时读 settings.MAX_SUB_QUERIES。直接 mutate 即生效。
【复用】collect_data + run_ragas_eval（官方 ragas 0.4.3）。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_precision_filters.py --limit 20
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

# (标签, rerank分数截断阈值, 子问题扇出上限)
CONFIGS = [
    ("baseline(0.0/5)", 0.0, 5),
    ("thr0.3/sub3", 0.3, 3),
    ("thr0.5/sub3", 0.5, 3),
]


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    gen = os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit)

    # --thresholds 指定时：构造 thr{t}/sub{--sub} 配置列表（固定扇出、精扫阀值）；否则用默认 CONFIGS
    if args.thresholds:
        _sub = args.sub
        configs = [(f"thr{t}/sub{_sub}", float(t), _sub) for t in args.thresholds.split(",")]
    else:
        configs = CONFIGS

    rows = []
    for label, thr, sub in configs:
        print("=" * 60)
        print(f"[{label}] RERANK_SCORE_THRESHOLD={thr}, MAX_SUB_QUERIES={sub}, 生产路由, {len(questions)} 题 ...")
        settings.RERANK_SCORE_THRESHOLD = thr
        settings.MAX_SUB_QUERIES = sub
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
            "label": label, "rerank_threshold": thr, "max_sub_queries": sub,
            "avg_contexts": round(avg_ctx, 2), "gen_seconds": round(gen_sec, 1),
            "faithfulness": ragas.get("faithfulness"),
            "answer_relevancy": ragas.get("answer_relevancy"),
            "context_precision": ragas.get("context_precision"),
            "context_recall": ragas.get("context_recall"),
            "overall": round(sum(vals) / len(vals), 4) if vals else None,
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "n": len(questions), "sweep": rows}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 90)
    print(f"精度过滤 A/B（官方 RAGAS, judge={judge}, n={len(questions)}, 生产路由）")
    print("%-16s %-9s %-7s %-7s %-7s %-7s %-8s %-8s" % (
        "config", "avg_ctx", "Faith", "AR", "CP", "CR", "Overall", "gen(s)"))
    for r in rows:
        print("%-16s %-9.2f %-7s %-7s %-7s %-7s %-8s %-8.1f" % (
            r["label"], r["avg_contexts"], _fmt(r["faithfulness"]), _fmt(r["answer_relevancy"]),
            _fmt(r["context_precision"]), _fmt(r["context_recall"]), _fmt(r["overall"]), r["gen_seconds"]))
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="精度过滤 A/B（rerank 截断 + 降扇出，官方 RAGAS）")
    ap.add_argument("--limit", type=int, default=20, help="题量（0=全部）")
    ap.add_argument("--judge-model", default=None, help="默认取 .env RAGAS_JUDGE_MODEL")
    ap.add_argument("--thresholds", default=None, help="逗号分隔的 RERANK_SCORE_THRESHOLD 精扫值（固定 --sub）；不填用默认 3 档")
    ap.add_argument("--sub", type=int, default=3, help="精扫时固定的 MAX_SUB_QUERIES")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "precision_filters_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
