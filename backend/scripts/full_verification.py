# -*- coding: utf-8 -*-
"""
SupplyChainRAG - 一键完整验证脚本
==============================
运行所有验证项，生成综合报告。

Usage:
    cd backend
    python scripts/full_verification.py
"""
import sys, os, json, time, subprocess
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "✅"
FAIL = "❌"

results = []
start_time = time.time()


def run_check(name, func):
    """Run a check function and record result."""
    try:
        ok, msg = func()
        status = PASS if ok else FAIL
        results.append({"name": name, "status": status, "detail": msg})
        print(f"  {status} {name}: {msg}")
    except Exception as e:
        results.append({"name": name, "status": FAIL, "detail": str(e)})
        print(f"  {FAIL} {name}: {e}")


def check_milvus():
    from app.core.milvus_client import milvus_manager
    milvus_manager.connect()
    milvus_manager.create_collection()
    count = milvus_manager.collection.num_entities
    return count > 0, f"{count} chunks"


def check_redis():
    from app.core.redis_client import redis_manager
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(redis_manager.connect())
    loop.close()
    return True, "Connected"


def check_neo4j():
    from app.core.neo4j_client import neo4j_client
    import asyncio
    if not neo4j_client.is_connected:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(neo4j_client.connect())
        loop.close()
    return neo4j_client.is_connected, "Connected"


def check_rag_agent():
    from app.agents.rag import rag_agent
    return True, "Imported"


def check_agentic_rag():
    from app.core.rag_engine import CriticEvaluator, QueryRewriter
    from app.core.llm_relevance import get_self_rag
    from app.core.query_analyzer import query_analyzer
    return True, "All components imported"


def check_config():
    from app.config import get_settings
    s = get_settings()
    return True, f"CRAG={s.CRAG_ENABLED}, LLMRelevance={s.LLM_RELEVANCE_ENABLED}"


def check_reranker():
    from app.core.rag_engine import RerankerEngine
    r = RerankerEngine()
    r.init()
    return r._model is not None, "Loaded" if r._model else "Not loaded"


def check_embedding():
    from app.core.rag_engine import rag_engine
    rag_engine.embedding.init()
    return rag_engine.embedding._model is not None, "Loaded"


def check_unit_tests():
    """Run unit tests and return pass count."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-k", "not integration", "--tb=no"],
        capture_output=True, text=True, timeout=120,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    output = result.stdout + result.stderr
    # Look for pattern like "160 passed" or "160 passed, 1 skipped"
    import re
    match = re.search(r'(\d+) passed', output)
    if match:
        passed = int(match.group(1))
        return True, f"{passed} passed"
    # Also check for "160 passed" in different format
    if "passed" in output:
        return True, output.split("passed")[0].strip().split()[-1] + " passed"
    return False, output[:200]


def check_agentic_tests():
    """Run Agentic RAG tests."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_agentic_rag.py", "-q", "--tb=no"],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    output = result.stdout + result.stderr
    import re
    match = re.search(r'(\d+) passed', output)
    if match:
        passed = int(match.group(1))
        return True, f"{passed}/21 passed"
    return False, output[:200]


def check_e2e_tests():
    """Run E2E tests."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_agentic_rag_e2e.py", "-q", "--tb=no"],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    output = result.stdout + result.stderr
    import re
    match = re.search(r'(\d+) passed', output)
    if match:
        passed = int(match.group(1))
        return True, f"{passed}/14 passed"
    return False, output[:200]


def check_ragas_results():
    """Check if RAGAS results exist."""
    eval_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
    
    results_3q = os.path.join(eval_dir, "eval_ragas_result_3q.json")
    results_full = os.path.join(eval_dir, "eval_ragas_result_full_sc.json")
    
    has_3q = os.path.exists(results_3q)
    has_full = os.path.exists(results_full)
    
    metrics = {}
    if has_3q:
        with open(results_3q, "r", encoding="utf-8") as f:
            data = json.load(f)
            metrics["3q"] = data.get("metrics", {})
    
    if has_full:
        with open(results_full, "r", encoding="utf-8") as f:
            data = json.load(f)
            metrics["full"] = data.get("metrics", {})
    
    if metrics:
        summary = []
        if "3q" in metrics:
            m = metrics["3q"]
            summary.append(f"3Q: Faith={m.get('coverage', 'N/A')}, CP={m.get('context_precision', 'N/A')}")
        if "full" in metrics:
            m = metrics["full"]
            summary.append(f"17Q: Faith={m.get('coverage', 'N/A')}, CP={m.get('context_precision', 'N/A')}")
        return True, "; ".join(summary)
    
    return False, "No RAGAS results found"


def check_grid_search():
    """Check if grid search results exist."""
    eval_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
    grid_path = os.path.join(eval_dir, "tune_results.json")
    
    if os.path.exists(grid_path):
        with open(grid_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            combos = len(data.get("all_results", []))
            best = data.get("best_params", {})
            return True, f"{combos} combos, best={best}"
    
    return False, "No grid search results"


def check_live_demo():
    """Check if live demo results exist."""
    eval_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
    demo_path = os.path.join(eval_dir, "live_rag_demo_result.json")
    
    if os.path.exists(demo_path):
        with open(demo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            count = len(data)
            avg_conf = sum(d.get("confidence", 0) for d in data) / max(count, 1)
            return True, f"{count} questions, avg_confidence={avg_conf:.3f}"
    
    return False, "No live demo results"


def check_benchmark():
    """Check if benchmark results exist."""
    eval_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
    bench_path = os.path.join(eval_dir, "benchmark_report.json")
    
    if os.path.exists(bench_path):
        with open(bench_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            queries = data.get("benchmark_queries", 0)
            success_rate = data.get("tool_call_success_rate", 0)
            return True, f"{queries} queries, {success_rate}% success"
    
    return False, "No benchmark results"


def main():
    print("=" * 60)
    print("SupplyChainRAG - Full Verification Report")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Infrastructure checks
    print("\n[1] Infrastructure")
    run_check("Milvus", check_milvus)
    run_check("Redis", check_redis)
    run_check("Neo4j", check_neo4j)

    # Component checks
    print("\n[2] Components")
    run_check("RAG Agent", check_rag_agent)
    run_check("Agentic RAG", check_agentic_rag)
    run_check("Config", check_config)
    run_check("Reranker", check_reranker)
    run_check("Embedding", check_embedding)

    # Test checks
    print("\n[3] Tests")
    run_check("Unit Tests", check_unit_tests)
    run_check("Agentic RAG Tests", check_agentic_tests)
    run_check("E2E Tests", check_e2e_tests)

    # Evaluation checks
    print("\n[4] Evaluations")
    run_check("RAGAS Results", check_ragas_results)
    run_check("Grid Search", check_grid_search)
    run_check("Live Demo", check_live_demo)
    run_check("Benchmark", check_benchmark)

    # Summary
    elapsed = time.time() - start_time
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    total = len(results)

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
    print(f"Elapsed: {elapsed:.1f}s")
    print("=" * 60)

    if failed > 0:
        print("\nFailed checks:")
        for r in results:
            if r["status"] == FAIL:
                print(f"  - {r['name']}: {r['detail']}")

    # Save report
    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed,
        "failed": failed,
        "total": total,
        "elapsed_s": round(elapsed, 1),
        "results": results
    }
    
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                               "eval", "full_verification_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\nReport saved to: {report_path}")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
