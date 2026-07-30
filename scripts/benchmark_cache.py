"""
benchmark_cache.py — 4 层缓存性能对比与并发验证脚本（阶段四验证用）

用法（需后端已启动，默认 http://localhost:8001）：
    python scripts/benchmark_cache.py                    # 缓存层级延迟对比
    python scripts/benchmark_cache.py --concurrency 10   # 并发压测 + /health 响应验证
    python scripts/benchmark_cache.py --base-url http://localhost:8001 --token <JWT>

输出：
    - 冷启动（全 miss）/ L1 命中（同问重放）/ L2 语义命中（近似问法）三档 P50/P95 延迟
    - /chat/cache/stats 各层命中率（需 admin token）
    - 并发模式下压测期间 /health 的响应延迟（验证事件循环未被阻塞）
"""
import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx

# 基准查询集：20 条（含精确编码类与语义类）
QUERIES = [
    "安全库存的标准是多少",
    "MAT-001 的库存情况",
    "采购订单的审批流程是什么",
    "供应商准入需要哪些资质",
    "质检不合格品如何处理",
    "生产计划的编制周期",
    "物料编码的命名规则",
    "库存ABC分类的标准",
    "供应商绩效评估的指标",
    "PO-20250101 订单状态",
    "入库验收的流程步骤",
    "呆滞物料的处理办法",
    "紧急采购的触发条件",
    "供应链风险预警机制",
    "工单创建的规范要求",
    "跨部门协作的流程",
    "成本核算的计算方法",
    "AQL 抽样标准是什么",
    "物流时效的考核标准",
    "MPS 主生产计划怎么编制",
]

# L2 语义命中验证：与上面语义相近但字面不同的问法
PARAPHRASES = [
    "安全库存标准值是多少",
    "查一下 MAT-001 库存",
    "采购单审批走什么流程",
]


def _pctl(latencies: list[float], p: float) -> float:
    if not latencies:
        return 0.0
    s = sorted(latencies)
    idx = min(len(s) - 1, int(len(s) * p))
    return s[idx]


async def _ask_once(client: httpx.AsyncClient, base_url: str, query: str, headers: dict) -> float:
    """发起一次流式问答，返回总耗时（秒）；SSE 全部读完为止"""
    t0 = time.perf_counter()
    async with client.stream(
        "POST", f"{base_url}/api/v1/chat/stream",
        json={"query": query}, headers=headers, timeout=120,
    ) as resp:
        async for _ in resp.aiter_lines():
            pass
    return time.perf_counter() - t0


async def run_latency_benchmark(base_url: str, headers: dict) -> None:
    async with httpx.AsyncClient() as client:
        print(f"\n== 冷启动（预期全 miss，{len(QUERIES)} 条）==")
        cold = []
        for q in QUERIES:
            try:
                cold.append(await _ask_once(client, base_url, q, headers))
            except Exception as e:
                print(f"  [跳过] {q[:20]}...: {e}")
        _report("冷启动", cold)

        print(f"\n== L1/查询缓存命中（同问重放，{len(QUERIES)} 条）==")
        warm = []
        for q in QUERIES:
            try:
                warm.append(await _ask_once(client, base_url, q, headers))
            except Exception as e:
                print(f"  [跳过] {q[:20]}...: {e}")
        _report("同问重放", warm)

        print(f"\n== L2 语义命中（近似问法，{len(PARAPHRASES)} 条）==")
        sem = []
        for q in PARAPHRASES:
            try:
                sem.append(await _ask_once(client, base_url, q, headers))
            except Exception as e:
                print(f"  [跳过] {q[:20]}...: {e}")
        _report("语义近似", sem)

        # 各层命中率（需 admin token）
        try:
            resp = await client.get(f"{base_url}/api/v1/chat/cache/stats", headers=headers)
            if resp.status_code == 200:
                print("\n== 缓存层命中率 (/chat/cache/stats) ==")
                print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
            else:
                print(f"\n[提示] cache/stats 返回 {resp.status_code}（需要 admin token）")
        except Exception as e:
            print(f"\n[提示] cache/stats 获取失败: {e}")


def _report(label: str, latencies: list[float]) -> None:
    if not latencies:
        print(f"  {label}: 无有效样本")
        return
    print(
        f"  {label}: n={len(latencies)} "
        f"P50={_pctl(latencies, 0.5)*1000:.0f}ms "
        f"P95={_pctl(latencies, 0.95)*1000:.0f}ms "
        f"avg={statistics.mean(latencies)*1000:.0f}ms"
    )


async def run_concurrency_benchmark(base_url: str, headers: dict, concurrency: int) -> None:
    """并发压测 /chat/stream，同时探测轻量端点延迟（验证事件循环未被阻塞）

    探针用 /chat/model/list（纯内存配置读取）而非 /health：
    /health 会同步检查 Milvus/Neo4j 连接，外部服务未启动时本身就耗时数秒，
    会把环境问题误报为事件循环阻塞。
    """
    probe_url = f"{base_url}/api/v1/chat/model/list"
    health_latencies: list[float] = []
    stop = asyncio.Event()

    async def _health_probe(client: httpx.AsyncClient):
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                await client.get(probe_url, timeout=10)
                health_latencies.append(time.perf_counter() - t0)
            except Exception:
                health_latencies.append(10.0)  # 超时按 10s 记
            await asyncio.sleep(0.2)

    async with httpx.AsyncClient() as client:
        # 空闲基线：压测前先采 3 个样本
        baseline = []
        for _ in range(3):
            t0 = time.perf_counter()
            try:
                await client.get(probe_url, timeout=10)
                baseline.append(time.perf_counter() - t0)
            except Exception:
                baseline.append(10.0)
        _report("探针空闲基线", baseline)

        probe_task = asyncio.create_task(_health_probe(client))

        print(f"\n== 并发压测: {concurrency} 路 /chat/stream ==")
        t0 = time.perf_counter()
        tasks = [
            _ask_once(client, base_url, QUERIES[i % len(QUERIES)], headers)
            for i in range(concurrency)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.perf_counter() - t0

        stop.set()
        await probe_task

        ok = [r for r in results if isinstance(r, float)]
        errors = [r for r in results if not isinstance(r, float)]
        print(f"  完成 {len(ok)}/{concurrency}，总耗时 {elapsed:.1f}s，失败 {len(errors)}")
        _report("并发问答", ok)
        _report("压测中探针", health_latencies)

        p95_health = _pctl(health_latencies, 0.95)
        if p95_health < 0.2:
            print(f"  [PASS] 压测期间轻量端点 P95={p95_health*1000:.0f}ms < 200ms（事件循环未被阻塞）")
        else:
            print(f"  [WARN] 压测期间轻量端点 P95={p95_health*1000:.0f}ms >= 200ms，对比空闲基线判断是否存在同步阻塞")


def main() -> int:
    parser = argparse.ArgumentParser(description="4 层缓存性能基准")
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--token", default="", help="JWT token（REQUIRE_AUTH_CHAT=true 或读取 cache/stats 时必需）")
    parser.add_argument("--concurrency", type=int, default=0, help=">0 时执行并发压测模式")
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    if args.concurrency > 0:
        asyncio.run(run_concurrency_benchmark(args.base_url, headers, args.concurrency))
    else:
        asyncio.run(run_latency_benchmark(args.base_url, headers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
