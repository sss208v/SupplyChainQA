"""Agent 工具调用基准测试 — 生成可写入简历的量化数据"""
import os, sys, json, time, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.agents.tool import tool_agent
from app.core.tool_metrics import tool_metrics
from app.core.milvus_client import milvus_manager

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
    ("create_ticket", "创建工单，标题 生产线停机，紧急"),
    ("create_ticket", "创建工单，物料 MAT-002 质量问题"),
    ("get_datetime", "现在几点了"),
    ("get_datetime", "今天是什么日期"),
    ("get_datetime", "当前时间"),
    ("get_knowledge", "新供应商准入需要什么资质"),
    ("get_knowledge", "库存ABC分类怎么划分的"),
    ("get_knowledge", "采购订单审批流程是什么"),
]

async def main():
    milvus_manager.connect()
    milvus_manager.create_collection()
    tool_metrics.clear()

    print(f"Running {len(TEST_QUERIES)} benchmark queries...")
    results = {"success": 0, "fail": 0, "durations": []}

    for i, (expected_tool, query) in enumerate(TEST_QUERIES):
        _t0 = time.perf_counter()
        try:
            result = await tool_agent.run(query, session_id=f"bench_{i}")
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
        except Exception as e:
            results["fail"] += 1
            print(f"  [{i+1:2d}/{len(TEST_QUERIES)}] FAIL: {type(e).__name__}: {str(e)[:60]}")

    n = len(TEST_QUERIES)
    success_rate = results["success"] / n * 100 if n else 0
    avg_duration = sum(results["durations"]) / len(results["durations"]) if results["durations"] else 0

    print(f"\n{'='*50}")
    print(f"Benchmark Results")
    print(f"{'='*50}")
    print(f"  Total queries:     {n}")
    print(f"  Tool call success: {results['success']}/{n} ({success_rate:.0f}%)")
    print(f"  Avg duration:      {avg_duration:.0f}ms")

    stats = tool_metrics.stats()
    print(f"\nPer-tool stats:")
    for name, s in sorted(stats.items()):
        if name != "_summary":
            print(f"  {name:20s}: {s['count']:2d} calls, avg {s['avg_ms']:5.0f}ms, success {s['success_rate']:.0%}")

    summary = stats.get("_summary", {})
    print(f"\n  Total tool calls: {summary.get('total_calls', 0)} across {summary.get('unique_tools', 0)} tools")

    report = {
        "benchmark_queries": n,
        "tool_call_success_rate": round(success_rate),
        "avg_duration_ms": round(avg_duration),
        "tool_stats": {k: v for k, v in stats.items() if k != "_summary"},
        "summary": summary,
    }
    with open("eval/benchmark_report.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\nSaved to eval/benchmark_report.json")

if __name__ == "__main__":
    asyncio.run(main())
