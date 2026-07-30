# -*- coding: utf-8 -*-
"""生成 prompt A/B —— Faithfulness 聚焦变体 vs 基线（复用已有 baseline_clean_x3.json）。

方法：运行时把 rag_agent.RAG_SYSTEM_PROMPT 换成变体，在 clean set 上跑 ×N 取均值，
对比已有基线 mean±std（只跑变体，基线复用，省一半时间）。judge=DeepSeek。

变体思路（针对 Faith 0.71 短板）：把"可靠溯源"置于"详尽"之上——新增"宁缺毋滥"
+ "输出前逐句自查、删除无据表述"，保留首句直答/逐句标注/禁编造。检索侧不变。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_prompt_ab.py --dataset eval\\eval_set_clean.json --repeats 3
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
from app.agents.rag import rag_agent
from app.core.rag_engine import rag_engine

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "overall"]

# Faith 聚焦变体：可靠溯源 > 详尽；保留 {chat_history}/{context} 占位
VARIANT_PROMPT = """你是一个供应链知识库问答助手。请严格根据参考资料回答问题，把"可靠溯源"放在"详尽"之上。

## 核心规则:
0. **仅基于参考资料** - 回答必须完全基于下面【参考资料】。禁止使用参考资料之外的领域知识、常识、推测。参考资料没提到的细节，回答中绝不出现。
1. **紧贴问题** - 第一句直接回答问题本身，不要铺垫、不要复述问题。
2. **有据才写** - 只要参考资料含相关信息就据此作答；仅当参考资料与问题完全无关时，才回答"这个问题知识库中暂无相关信息"。
3. **逐句标注来源** - 每个关键事实、数字、流程都必须标注 [1] [2] 等编号；回答结尾列出引用，格式：[编号] 文档名称 — 章节。
4. **禁止编造** - 严禁添加参考资料中没有明确出现的信息、数字、百分比、步骤。不确定就不写。
5. **宁缺毋滥** - 不要为了"全面"而补充参考资料没有明确支持的内容；有据可查的才写，无据的宁可省略。
6. **输出前自查** - 逐句检查：每个陈述能否在参考资料中找到直接依据？删除任何找不到依据的表述后再输出。

## 当前对话历史:
{chat_history}

## 参考资料:
{context}"""


def _fmt(v):
    return f"{v:.4f}" if isinstance(v, (int, float)) else "NaN"


async def run(args):
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit, args.dataset)

    # 运行时替换生成 prompt 为变体（实例属性遮蔽类属性）
    rag_agent.RAG_SYSTEM_PROMPT = VARIANT_PROMPT
    print(f"[variant] Faith 聚焦变体 | {len(questions)} 题 × {args.repeats} | gen={gen} judge={judge}")

    runs = []
    for r in range(1, args.repeats + 1):
        print("=" * 60)
        print(f"[variant run {r}/{args.repeats}] ...")
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
        row = {**{m: ragas.get(m) for m in _METRICS[:-1]}, "overall": overall,
               "gen_seconds": round(gen_sec, 1)}
        runs.append(row)
        print(f"  run{r}: " + " ".join(f"{m}={_fmt(row.get(m))}" for m in _METRICS))

    summary = {}
    for m in _METRICS:
        xs = [r[m] for r in runs if isinstance(r.get(m), (int, float))]
        if xs:
            summary[m] = {"mean": round(st.mean(xs), 4),
                          "std": round(st.pstdev(xs), 4) if len(xs) > 1 else 0.0}

    # 载入基线做对比
    base = {}
    if os.path.exists(args.baseline):
        base = json.load(open(args.baseline, encoding="utf-8")).get("summary", {})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "gen_model": gen, "n": len(questions),
                   "repeats": args.repeats, "variant_runs": runs, "variant_summary": summary,
                   "baseline_summary": base}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print(f"生成 prompt A/B（mean±std, n={len(questions)}×{args.repeats}）")
    print("%-20s %-18s %-18s %-8s" % ("metric", "baseline", "variant", "Δmean"))
    for m in _METRICS:
        b, v = base.get(m), summary.get(m)
        if v:
            bs = f"{b['mean']:.4f}±{b['std']:.4f}" if b else "N/A"
            vs = f"{v['mean']:.4f}±{v['std']:.4f}"
            d = f"{v['mean'] - b['mean']:+.4f}" if b else "N/A"
            print("%-20s %-18s %-18s %-8s" % (m, bs, vs, d))
    print(f"\nSaved -> {args.out}")
    print("[NOTE] 判定：变体在 Faith 上稳健优于基线(超 ±std)才改 rag.py；否则保留，记无增益。")


def main():
    ap = argparse.ArgumentParser(description="生成 prompt A/B（Faith 聚焦变体 vs 基线）")
    ap.add_argument("--dataset", default=os.path.join(EVAL_DIR, "eval_set_clean.json"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gen-model", default=None)
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--baseline", default=os.path.join(EVAL_DIR, "baseline_clean_x3.json"))
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "prompt_ab_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
