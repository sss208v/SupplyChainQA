# -*- coding: utf-8 -*-
"""top_k 参数调优实验 —— 扫描检索 top_k，用【官方 RAGAS】度量，产出 CP/CR 权衡曲线。

【控制变量】固定生成模型 + 固定 judge + 同一题集 + 强制 full 检索策略，仅 top_k 作为自变量。

【为什么要 monkeypatch】项目实际 top_k 由 query_analyzer 的策略配置决定
（STRATEGIES: light=3 / standard=5 / full=8），settings.RERANK_TOP_K 仅作兜底。
因此直接改 settings.RERANK_TOP_K 无效；本脚本 monkeypatch get_strategy_config
统一返回 full 流程 + 指定 top_k，保证只有 top_k 变化。

【复用】直接复用 run_comprehensive_ragas.py 的 collect_data（全链路生成）与
run_ragas_eval（官方 ragas 0.4.3 LLM-as-Judge），不引入任何 proxy。

【judge 配置】走 backend/.env 的 RAGAS_JUDGE_*（推荐 DeepSeek 官方 deepseek-v4-flash 非思考模式）。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_top_k.py --values 3,5,8,12 --limit 20
  venv\\Scripts\\python.exe eval\\tune_top_k.py --values 5,8 --limit 3   # 快速冒烟
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.run_comprehensive_ragas import collect_data, run_ragas_eval, _questions, LLAMA_MODEL
from app.core import query_analyzer as _qa
from app.core.rag_engine import rag_engine

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _force_topk(k: int):
    """强制所有查询走 standard 策略 + 指定 top_k（仅 top_k 为自变量）。

    用 standard 而非 full：保留 reranker 精排、但跳过 Self-RAG 的逐块 LLM 过滤。
    ① 保证 retrieved_contexts 恰为 top_k（Self-RAG 会二次过滤、混淆 top_k 轴）；
    ② 大幅加快本地生成（省去每块一次 LLM 判定）。
    """
    base = dict(_qa.STRATEGIES["standard"])
    base["top_k"] = k
    base["use_reranker"] = True
    _qa.query_analyzer.get_strategy_config = lambda strategy: dict(base)


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    ks = [int(x) for x in args.values.split(",")]
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit)

    rows = []
    for k in ks:
        print("=" * 60)
        print(f"[top_k={k}] 强制 standard 策略(reranker开/Self-RAG关) + top_k={k}，生成 {len(questions)} 题 ...")
        _force_topk(k)
        if hasattr(rag_engine, "_query_cache"):
            try:
                rag_engine._query_cache.clear()
            except Exception:
                pass

        t0 = time.time()
        eval_data = await collect_data(questions, gen)
        gen_sec = time.time() - t0
        avg_ctx = sum(len(d["retrieved_contexts"]) for d in eval_data) / len(eval_data)

        with open(os.path.join(EVAL_DIR, f"_topk_raw_{k}.json"), "w", encoding="utf-8") as f:
            json.dump(eval_data, f, ensure_ascii=False, indent=2)

        ragas = run_ragas_eval(eval_data, judge)
        vals = [v for v in ragas.values() if v is not None]
        overall = round(sum(vals) / len(vals), 4) if vals else None
        rows.append({
            "top_k": k,
            "avg_contexts": round(avg_ctx, 2),
            "gen_seconds": round(gen_sec, 1),
            "faithfulness": ragas.get("faithfulness"),
            "answer_relevancy": ragas.get("answer_relevancy"),
            "context_precision": ragas.get("context_precision"),
            "context_recall": ragas.get("context_recall"),
            "overall": overall,
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "gen_model": gen, "n": len(questions), "sweep": rows}, f,
                  ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print(f"top_k 调优对照（官方 RAGAS, judge={judge}, n={len(questions)}）")
    print("%-6s %-9s %-7s %-7s %-7s %-7s %-8s %-8s" % (
        "top_k", "avg_ctx", "Faith", "AR", "CP", "CR", "Overall", "gen(s)"))
    for r in rows:
        print("%-6d %-9.2f %-7s %-7s %-7s %-7s %-8s %-8.1f" % (
            r["top_k"], r["avg_contexts"], _fmt(r["faithfulness"]), _fmt(r["answer_relevancy"]),
            _fmt(r["context_precision"]), _fmt(r["context_recall"]), _fmt(r["overall"]), r["gen_seconds"]))
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="top_k 调优实验（官方 RAGAS）")
    ap.add_argument("--values", default="3,5,8,12", help="逗号分隔的 top_k 候选")
    ap.add_argument("--limit", type=int, default=20, help="题量（0=全部）")
    ap.add_argument("--gen-model", default=None, help="覆盖生成模型名")
    ap.add_argument("--judge-model", default=None, help="覆盖 judge 模型名（默认取 .env RAGAS_JUDGE_MODEL）")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "topk_sweep_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
