# -*- coding: utf-8 -*-
"""多跑取均值 —— 同一 config 跑 N 次全链路(生成+官方 RAGAS)，输出每指标 mean±std。

目的：把 20 题单跑 ±0.03 的噪声压到均值 ±0.01，建立可信基线，才能分辨细改动。
用当前 .env 生产配置(不强制策略)；judge=DeepSeek。复用 run_comprehensive_ragas 的底座。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\eval_repeat.py --dataset eval\\eval_set_clean.json --repeats 3
"""
import argparse
import asyncio
import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.run_comprehensive_ragas import collect_data, run_ragas_eval, _questions, LLAMA_MODEL
from app.core.rag_engine import rag_engine

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "overall"]


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit, args.dataset)
    print(f"多跑取均值：{len(questions)} 题 × {args.repeats} 次 | gen={gen} judge={judge} | dataset={args.dataset or '内置'}")

    runs = []
    for r in range(1, args.repeats + 1):
        print("=" * 60)
        print(f"[run {r}/{args.repeats}] 生成 {len(questions)} 题 ...")
        if hasattr(rag_engine, "_query_cache"):
            try:
                rag_engine._query_cache.clear()
            except Exception:
                pass
        t0 = time.time()
        eval_data = await collect_data(questions, gen)
        gen_sec = time.time() - t0
        ragas = run_ragas_eval(eval_data, judge)
        vals = [v for v in ragas.values() if v is not None]
        overall = round(sum(vals) / len(vals), 4) if vals else None
        avg_ctx = sum(len(d["retrieved_contexts"]) for d in eval_data) / len(eval_data)
        row = {**{m: ragas.get(m) for m in _METRICS[:-1]}, "overall": overall,
               "avg_contexts": round(avg_ctx, 2), "gen_seconds": round(gen_sec, 1)}
        runs.append(row)
        print(f"  run{r}: " + " ".join(f"{m}={_fmt(row.get(m))}" for m in _METRICS))

    # 每指标 mean±std
    summary = {}
    for m in _METRICS:
        xs = [r[m] for r in runs if isinstance(r.get(m), (int, float))]
        if xs:
            summary[m] = {"mean": round(st.mean(xs), 4),
                          "std": round(st.pstdev(xs), 4) if len(xs) > 1 else 0.0}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "gen_model": gen, "n": len(questions),
                   "repeats": args.repeats, "dataset": args.dataset, "runs": runs,
                   "summary": summary}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"可信基线（mean±std, n={len(questions)}×{args.repeats}, dataset={args.dataset or '内置'}）")
    for m in _METRICS:
        s = summary.get(m)
        if s:
            print(f"  {m:20s}: {s['mean']:.4f} ± {s['std']:.4f}")
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="多跑取均值可信基线（官方 RAGAS）")
    ap.add_argument("--dataset", default=None, help="评测集 JSON（默认用内置）")
    ap.add_argument("--repeats", type=int, default=3, help="重复次数")
    ap.add_argument("--limit", type=int, default=0, help="题量（0=全部）")
    ap.add_argument("--gen-model", default=None)
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "eval_repeat_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
