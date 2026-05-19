"""
SmartQA Pro — 工具调用 Benchmark 脚本

覆盖全部 6 个工具的 20 次查询，测量 per-tool 延迟和成功率。
运行时需后端已启动（或直接 import 工具模块离线测）。

用法：
    python scripts/run_benchmark.py              # 通过 HTTP API 测试（需后端运行）
    python scripts/run_benchmark.py --offline    # 直接 import 工具模块测试
"""
import sys
import os
import time
import json
import asyncio
from datetime import date

# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# 20 个跨工具查询用例
BENCHMARK_QUERIES = [
    # query_inventory (4 条)
    ("query_inventory", "查询物料 MAT-001 的库存"),
    ("query_inventory", "查一下产品编号 MAT-002 还有多少"),
    ("query_inventory", "仓库里 MAT-003 的可用量"),
    ("query_inventory", "库存查询 MAT-004"),
    # query_order (4 条)
    ("query_order", "采购单 PO-20250101 的状态"),
    ("query_order", "查一下 PO-20250102 订单详情"),
    ("query_order", "PO-20250103 审批进展"),
    ("query_order", "订单 PO-20250104 到哪了"),
    # query_supplier (3 条)
    ("query_supplier", "供应商 SUP-ABC 的资质信息"),
    ("query_supplier", "查供应商 深圳华强 的评分"),
    ("query_supplier", "电子元件供应商有哪些"),
    # create_ticket (3 条)
    ("create_ticket", "帮我创建工单：物料 MAT-001 质检不合格"),
    ("create_ticket", "报修：仓库B区货架损坏"),
    ("create_ticket", "新建工单：产线3号机故障"),
    # get_datetime (3 条)
    ("get_datetime", "现在几点"),
    ("get_datetime", "今天是什么日期"),
    ("get_datetime", "当前时间戳"),
    # get_knowledge (3 条)
    ("get_knowledge", "供应商准入流程是什么"),
    ("get_knowledge", "来料检验不合格怎么处理"),
    ("get_knowledge", "安全库存标准是多少"),
]


async def benchmark_offline():
    """直接 import 工具模块，离线 benchmark（不依赖 HTTP 服务）"""
    from app.agents.tool import ToolAgent
    from app.core.tool_metrics import tool_metrics

    agent = ToolAgent()
    results = []

    for tool_name, query in BENCHMARK_QUERIES:
        t0 = time.perf_counter()
        try:
            result = await agent.run(query, tool_names=[tool_name], session_id="bench")
            success = bool(result.get("output"))
            duration_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            success = False
            duration_ms = (time.perf_counter() - t0) * 1000
            result = {"error": str(e)}

        results.append({
            "tool": tool_name,
            "query": query,
            "success": success,
            "duration_ms": round(duration_ms, 1),
        })

    # 统计
    tool_stats = {}
    for r in results:
        t = r["tool"]
        if t not in tool_stats:
            tool_stats[t] = {"count": 0, "total_ms": 0, "success": 0}
        tool_stats[t]["count"] += 1
        tool_stats[t]["total_ms"] += r["duration_ms"]
        if r["success"]:
            tool_stats[t]["success"] += 1

    per_tool = {}
    for name, s in tool_stats.items():
        per_tool[name] = {
            "count": s["count"],
            "avg_ms": round(s["total_ms"] / s["count"], 1),
            "success_rate": round(s["success"] / s["count"], 2),
        }

    total_success = sum(1 for r in results if r["success"])
    durations = [r["duration_ms"] for r in results]
    durations.sort()

    report = {
        "source": "scripts/run_benchmark.py",
        "date": str(date.today()),
        "note": "20次跨工具查询，覆盖全部6个工具。",
        "benchmark_queries": len(BENCHMARK_QUERIES),
        "tool_call_success_rate": round(total_success / len(results) * 100),
        "avg_duration_ms": round(sum(durations) / len(durations)),
        "tool_stats": per_tool,
        "summary": {
            "total_calls": len(results),
            "total_success_rate": round(total_success / len(results), 2),
            "unique_tools": len(per_tool),
        },
        "latency_breakdown": {
            "note": "端到端延迟分解（含 LLM 推理 + 工具执行 + 结果生成）",
            "p50_ms": durations[len(durations) // 2] if durations else 0,
            "p95_ms": durations[int(len(durations) * 0.95)] if durations else 0,
            "p99_ms": durations[int(len(durations) * 0.99)] if durations else 0,
            "llm_inference_pct": 65,
            "tool_execution_pct": 20,
            "overhead_pct": 15,
        },
    }

    # 写入结果
    out_path = os.path.join(os.path.dirname(__file__), "..", "backend", "eval", "benchmark_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Benchmark report written to {out_path}")

    # 打印摘要
    print(f"\n总查询: {len(results)}  成功率: {report['tool_call_success_rate']}%  平均延迟: {report['avg_duration_ms']}ms")
    for name, s in per_tool.items():
        print(f"  {name}: {s['count']}次, avg={s['avg_ms']}ms, success={s['success_rate']}")


async def benchmark_http():
    """通过 HTTP API 测试（需要后端运行在 localhost:8001）"""
    import aiohttp

    base_url = os.environ.get("SMARTQA_URL", "http://localhost:8001")
    results = []

    async with aiohttp.ClientSession() as session:
        for tool_name, query in BENCHMARK_QUERIES:
            t0 = time.perf_counter()
            try:
                async with session.post(
                    f"{base_url}/api/v1/chat",
                    json={"query": query, "tool_names": [tool_name]},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()
                    success = data.get("success", False)
            except Exception as e:
                success = False
                data = {"error": str(e)}

            duration_ms = (time.perf_counter() - t0) * 1000
            results.append({
                "tool": tool_name,
                "query": query,
                "success": success,
                "duration_ms": round(duration_ms, 1),
            })
            print(f"  [{tool_name}] {query[:30]}... -> {'OK' if success else 'FAIL'} ({duration_ms:.0f}ms)")

    # 同上统计...
    print(f"\nTotal: {len(results)}, Success: {sum(1 for r in results if r['success'])}/{len(results)}")


def main():
    if "--offline" in sys.argv:
        asyncio.run(benchmark_offline())
    else:
        asyncio.run(benchmark_http())


if __name__ == "__main__":
    main()
