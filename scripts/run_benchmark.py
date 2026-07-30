"""Agent 工具调用基准测试 — 支持 performance / quality / both 模式

Usage:
    python scripts/run_benchmark.py                          # 默认 quality 模式
    python scripts/run_benchmark.py --mode performance       # 禁用 Reranker，仅测速度
    python scripts/run_benchmark.py --mode quality           # 启用 Reranker + RAGAS 评估
    python scripts/run_benchmark.py --mode both              # 先 performance 再 quality，输出对比
"""
import os, sys, json, time, asyncio, argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.agents.tool import tool_agent
from app.core.tool_metrics import tool_metrics
from app.core.milvus_client import milvus_manager
from app.config import get_settings

TEST_QUERIES = [
    ("query_inventory", "查一下物料 MAT-001 的库存"),
    ("query_inventory", "MAT-002 还有多少件"),
    ("query_inventory", "帮我看看 MAT-003 的库存情况"),
    ("query_inventory", "MAT-001 的库存够不够安全库存"),
    ("query_order", "采购单 PO-20250601 的状态是什么"),
    ("query_order", "PO-20250602 到货了没有"),
    ("query_order", "查一下 PO-20250601 的订单明细"),
    ("query_order", "PO-20250603 总共多少钱"),
    ("query_supplier", "供应商 SUP-001 的信息"),
    ("query_supplier", "SUP-002 的合作年限是多少"),
    ("query_supplier", "查一下供应商 SUP-003"),
    ("create_ticket", "创建一个工单，物料 MAT-001 缺货 50 件"),
    ("create_ticket", "创建工单，标题：生产线停机，紧急"),
    ("create_ticket", "创建工单，物料 MAT-002 质量问题"),
    ("get_datetime", "现在几点了"),
    ("get_datetime", "今天是什么日期"),
    ("get_datetime", "当前时间"),
    ("get_knowledge", "新供应商准入需要什么资质"),
    ("get_knowledge", "库存ABC分类怎么划分的"),
    ("get_knowledge", "采购订单审批流程是什么"),
]

EVAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "eval")


async def run_tool_benchmark(mode_label: str) -> dict:
    """运行 20 条工具调用 benchmark，返回统计结果"""
    milvus_manager.connect()
    milvus_manager.create_collection()
    tool_metrics.clear()

    print(f"\n{'='*60}")
    print(f"  Benchmark [{mode_label}] — {len(TEST_QUERIES)} queries")
    print(f"{'='*60}")

    results = {"success": 0, "fail": 0, "durations": []}
    query_results = []

    for i, (expected_tool, query) in enumerate(TEST_QUERIES):
        _t0 = time.perf_counter()
        try:
            result = await tool_agent.run(query, session_id=f"bench_{mode_label}_{i}")
            duration = (time.perf_counter() - _t0) * 1000
            tool_calls = result.get("tool_calls", [])
            tools_used = [tc.get("tool", "?") for tc in tool_calls]
            has_answer = len(result.get("answer", "")) > 50

            if tool_calls and has_answer:
                results["success"] += 1
                results["durations"].append(duration)
                status = "OK"
            elif tool_calls:
                results["success"] += 1
                results["durations"].append(duration)
                status = "TOOL_ONLY"
            else:
                results["fail"] += 1
                status = "NO_TOOL"
            print(f"  [{i+1:2d}/{len(TEST_QUERIES)}] {status} {duration:.0f}ms  {','.join(tools_used):30s}  {query[:40]}")
            query_results.append({"query": query, "status": status, "duration_ms": round(duration), "tools": tools_used})
        except Exception as e:
            results["fail"] += 1
            print(f"  [{i+1:2d}/{len(TEST_QUERIES)}] FAIL: {type(e).__name__}: {str(e)[:60]}")
            query_results.append({"query": query, "status": "FAIL", "error": str(e)[:80]})

    n = len(TEST_QUERIES)
    success_rate = results["success"] / n * 100 if n else 0
    avg_duration = sum(results["durations"]) / len(results["durations"]) if results["durations"] else 0

    stats = tool_metrics.stats()
    summary = stats.get("_summary", {})

    print(f"\n  [{mode_label}] Tool call success: {results['success']}/{n} ({success_rate:.0f}%)")
    print(f"  [{mode_label}] Avg duration:      {avg_duration:.0f}ms")
    print(f"  [{mode_label}] Total tool calls:  {summary.get('total_calls', 0)} across {summary.get('unique_tools', 0)} tools")

    return {
        "mode": mode_label,
        "reranker_enabled": get_settings().RERANKER_ENABLED,
        "benchmark_queries": n,
        "tool_call_success_rate": round(success_rate),
        "avg_duration_ms": round(avg_duration),
        "tool_stats": {k: v for k, v in stats.items() if k != "_summary"},
        "summary": summary,
        "query_results": query_results,
    }


async def run_ragas_eval() -> dict:
    """运行 RAGAS 质量评估（20 题，DeepSeek 作为 Judge LLM）"""
    print(f"\n{'='*60}")
    print(f"  RAGAS Quality Evaluation — 20 questions")
    print(f"{'='*60}")

    settings = get_settings()
    if not settings.DEEPSEEK_API_KEY:
        print("  [SKIP] DEEPSEEK_API_KEY not set, skipping RAGAS evaluation")
        return {"ragas_skipped": True, "reason": "DEEPSEEK_API_KEY not set"}

    sys.path.insert(0, EVAL_DIR)
    from test_dataset import TEST_QA_PAIRS
    from app.agents.rag import rag_agent

    eval_data = []
    for i, pair in enumerate(TEST_QA_PAIRS):
        question = pair["question"]
        reference = pair["reference_answer"]
        print(f"  [{i+1:2d}/{len(TEST_QA_PAIRS)}] Q: {question[:50]}...")

        try:
            start = time.time()
            query_type = rag_agent._classify_query(question)
            search_queries = await rag_agent._prepare_queries(question, query_type)

            from app.core.rag_engine import rag_engine
            all_results = []
            for sq in search_queries:
                result = rag_engine.search(sq, top_k=settings.RERANK_TOP_K)
                all_results.extend(result.get("results", []))

            seen = set()
            unique_results = []
            for r in all_results:
                chunk_id = r.get("chunk_id", "")
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    unique_results.append(r)

            retrieved_contexts = [r.get("content", "") for r in unique_results]
            rag_result = await rag_agent.answer(query=question, session_id=None)
            response = rag_result["answer"]
            elapsed = time.time() - start
            print(f"       A: {response[:80]}... ({elapsed:.1f}s)")

            eval_data.append({
                "user_input": question,
                "response": response,
                "reference": reference,
                "retrieved_contexts": retrieved_contexts,
            })
        except Exception as e:
            print(f"       ERROR: {type(e).__name__}: {e}")
            eval_data.append({
                "user_input": question,
                "response": f"ERROR: {e}",
                "reference": reference,
                "retrieved_contexts": [],
            })

    success = sum(1 for d in eval_data if not d["response"].startswith("ERROR"))
    print(f"\n  RAG data collection: {success}/{len(eval_data)} success")
    if success == 0:
        return {"ragas_skipped": True, "reason": "All RAG queries failed"}

    # Run RAGAS metrics
    try:
        from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
        from ragas.metrics._faithfulness import Faithfulness
        from ragas.metrics._answer_relevance import AnswerRelevancy
        from ragas.metrics._context_precision import ContextPrecision
        from ragas.metrics._context_recall import ContextRecall
        from langchain_openai import ChatOpenAI

        judge_llm = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            base_url=settings.DEEPSEEK_BASE_URL,
            api_key=settings.DEEPSEEK_API_KEY,
            temperature=0.0,
            max_tokens=512,
            max_retries=3,
        )
        from app.core.rag_engine import rag_engine
        rag_engine.embedding.init()
        judge_embeddings = rag_engine.embedding._model

        # Filter out ERROR samples
        valid_data = [d for d in eval_data if not d["response"].startswith("ERROR")]
        samples = [SingleTurnSample(
            user_input=item["user_input"],
            response=item["response"],
            reference=item["reference"],
            retrieved_contexts=item["retrieved_contexts"],
        ) for item in valid_data]

        dataset = EvaluationDataset(samples=samples)
        metrics = [Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()]
        print(f"\n  Running RAGAS on {len(samples)} samples (Judge: {settings.DEEPSEEK_MODEL})...")
        result = evaluate(dataset=dataset, metrics=metrics, llm=judge_llm, embeddings=judge_embeddings)
        df = result.to_pandas()

        ragas_scores = {}
        for col in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            if col in df.columns:
                ragas_scores[col] = round(float(df[col].dropna().mean()), 4)

        print(f"\n  RAGAS Results:")
        for k, v in ragas_scores.items():
            print(f"    {k:25s}: {v:.4f}")

        return {"ragas_scores": ragas_scores, "ragas_samples": len(samples)}

    except ImportError as e:
        print(f"\n  [SKIP] RAGAS not installed: {e}")
        return {"ragas_skipped": True, "reason": f"Import error: {e}"}
    except Exception as e:
        print(f"\n  [ERROR] RAGAS evaluation failed: {e}")
        import traceback
        traceback.print_exc()
        return {"ragas_skipped": True, "reason": str(e)[:100]}


async def main():
    parser = argparse.ArgumentParser(description="Supply Chain QA Benchmark — performance / quality / both")
    parser.add_argument("--mode", choices=["performance", "quality", "both"], default="quality",
                        help="performance: RERANKER_ENABLED=false, speed only; quality: RERANKER_ENABLED=true + RAGAS; both: run both and compare")
    parser.add_argument("--output", default=None, help="Output JSON path (default: eval/benchmark_report.json)")
    args = parser.parse_args()

    settings = get_settings()
    output_path = args.output or os.path.join(EVAL_DIR, "benchmark_report.json")

    t0 = time.time()
    report = {}

    if args.mode == "performance":
        settings.RERANKER_ENABLED = False
        report = await run_tool_benchmark("performance")

    elif args.mode == "quality":
        settings.RERANKER_ENABLED = True
        report = await run_tool_benchmark("quality")
        ragas = await run_ragas_eval()
        report.update(ragas)

    elif args.mode == "both":
        # Performance mode
        settings.RERANKER_ENABLED = False
        perf_report = await run_tool_benchmark("performance")

        # Quality mode
        settings.RERANKER_ENABLED = True
        quality_report = await run_tool_benchmark("quality")
        ragas = await run_ragas_eval()
        quality_report.update(ragas)

        # Comparison
        speedup = quality_report["avg_duration_ms"] / perf_report["avg_duration_ms"] if perf_report["avg_duration_ms"] > 0 else 0
        report = {
            "mode": "both",
            "performance": perf_report,
            "quality": quality_report,
            "comparison": {
                "speedup_factor": round(speedup, 1),
                "performance_avg_ms": perf_report["avg_duration_ms"],
                "quality_avg_ms": quality_report["avg_duration_ms"],
            },
            "benchmark_queries": len(TEST_QUERIES),
        }
        if "ragas_scores" in quality_report:
            report["ragas_metrics"] = quality_report["ragas_scores"]

    report["total_time_s"] = round(time.time() - t0, 1)
    report["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {output_path}")
    print(f"Total time: {report['total_time_s']}s")


if __name__ == "__main__":
    asyncio.run(main())
