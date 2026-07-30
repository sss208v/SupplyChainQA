# -*- coding: utf-8 -*-
"""
SupplyChainRAG - Agentic RAG 综合验证脚本
======================================
一键验证所有 Agentic RAG 组件的正确性。

Usage:
    cd backend
    python scripts/verify_agentic_rag.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "\u2705 PASS"
FAIL = "\u274c FAIL"
SKIP = "\u23ed\ufe0f SKIP"

results = []


def check(name, condition, detail=""):
    if condition:
        print(f"  {PASS}: {name}")
        results.append((name, True, detail))
    else:
        print(f"  {FAIL}: {name} {detail}")
        results.append((name, False, detail))


def skip(name, reason=""):
    print(f"  {SKIP}: {name} {reason}")
    results.append((name, None, reason))


print("=" * 60)
print("SupplyChainRAG - Agentic RAG Verification")
print("=" * 60)

# ----------------------------------------------------------
# 1. CriticEvaluator
# ----------------------------------------------------------
print("\n[1] CriticEvaluator")
try:
    from app.core.rag_engine import CriticEvaluator
    # 1.1 extract_keywords
    kw = CriticEvaluator.extract_keywords("MAT-001 \u5b89\u5168\u5e93\u5b58")
    check("extract_keywords \u63d0\u53d6\u5b9e\u4f53", len(kw) >= 2)
    
    # 1.2 evaluate high quality
    r = CriticEvaluator.evaluate(
        "MAT-001 \u5b89\u5168\u5e93\u5b58",
        [{"content": "MAT-001 \u5b89\u5168\u5e93\u5b58100\u4ef6", "rerank_score": 0.8, "chunk_id": "c1"}]
    )
    check("evaluate \u9ad8\u8d28\u91cf\u8bc4\u4f30", r["quality"] in ["high", "medium"])
    check("evaluate keyword_coverage", r["keyword_coverage"] > 0.3)
    
    # 1.3 evaluate low quality
    r2 = CriticEvaluator.evaluate(
        "MAT-001 \u5b89\u5168\u5e93\u5b58",
        [{"content": "\u4f9b\u5e94\u5546\u51c6\u5165\u6d41\u7a0b", "rerank_score": 0.1, "chunk_id": "c2"}]
    )
    check("evaluate \u4f4e\u8d28\u91cf\u8bc4\u4f30", r2["quality"] == "low")
    check("evaluate \u4f4e\u8d28\u91cf needs_retry", r2["needs_retry"] is True)
    
    # 1.4 evaluate empty
    r3 = CriticEvaluator.evaluate("\u6d4b\u8bd5", [])
    check("evaluate \u7a7a\u7ed3\u679c", r3["quality"] == "low")
except Exception as e:
    check("CriticEvaluator \u5bfc\u5165", False, str(e))

# ----------------------------------------------------------
# 2. QueryRewriter
# ----------------------------------------------------------
print("\n[2] QueryRewriter")
try:
    from app.core.rag_engine import QueryRewriter
    # 2.1 expand_search
    rw = QueryRewriter.rewrite_for_retry("MAT-001 \u7684\u5b89\u5168\u5e93\u5b58\u662f\u591a\u5c11", [], "expand_search")
    check("expand_search \u79fb\u9664\u7591\u95ee\u8bcd", "\u662f\u591a\u5c11" not in rw)
    check("expand_search \u4fdd\u7559\u5b9e\u4f53", "MAT-001" in rw)
    
    # 2.2 rewrite_query
    rw2 = QueryRewriter.rewrite_for_retry(
        "MAT-001 \u5e93\u5b58",
        [{"content": "\u5b89\u5168\u5e93\u5b58\u516c\u5f0f \u65e5\u5747\u6d88\u8017", "rerank_score": 0.5}],
        "rewrite_query"
    )
    check("rewrite_query \u8865\u5145\u5173\u952e\u8bcd", len(rw2) >= len("MAT-001 \u5e93\u5b58"))
except Exception as e:
    check("QueryRewriter \u5bfc\u5165", False, str(e))

# ----------------------------------------------------------
# 3. Adaptive Strategy Upgrade
# ----------------------------------------------------------
print("\n[3] Adaptive Strategy Upgrade")
try:
    from app.core.query_analyzer import query_analyzer
    check("light+medium->standard", query_analyzer.upgrade_strategy("light", "medium") == "standard")
    check("light+low->full", query_analyzer.upgrade_strategy("light", "low") == "full")
    check("standard+low->full", query_analyzer.upgrade_strategy("standard", "low") == "full")
    check("full+low->full", query_analyzer.upgrade_strategy("full", "low") == "full")
    check("light+high->light", query_analyzer.upgrade_strategy("light", "high") == "light")
except Exception as e:
    check("upgrade_strategy \u5bfc\u5165", False, str(e))

# ----------------------------------------------------------
# 4. CRAG Config
# ----------------------------------------------------------
print("\n[4] CRAG Config")
try:
    from app.config import get_settings
    s = get_settings()
    check("CRAG_ENABLED", s.CRAG_ENABLED is True)
    check("CRAG_MAX_RETRIES", s.CRAG_MAX_RETRIES == 1)
    check("CRAG_RELEVANCE_THRESHOLD", s.CRAG_RELEVANCE_THRESHOLD == 0.4)
except Exception as e:
    check("CRAG Config", False, str(e))

# ----------------------------------------------------------
# 5. rag_agent import
# ----------------------------------------------------------
print("\n[5] rag_agent Import")
try:
    from app.agents.rag import rag_agent
    check("rag_agent \u5bfc\u5165", True)
    check("rag_agent \u6709 CRAG \u903b\u8f91", hasattr(rag_agent, "rag"))
except Exception as e:
    check("rag_agent \u5bfc\u5165", False, str(e))

# ----------------------------------------------------------
# 6. Graph Critic Logic
# ----------------------------------------------------------
print("\n[6] Graph Critic Logic")
try:
    from app.core.rag_engine import CriticEvaluator
    # High overlap
    qk = CriticEvaluator.extract_keywords("MAT-001 \u4f9b\u5e94\u5546")
    gk = CriticEvaluator.extract_keywords("MAT-001 \u7531\u4f9b\u5e94\u5546 SUP-001 \u4f9b\u5e94")
    overlap = len(qk & gk) / max(len(qk), 1)
    check("Graph \u9ad8\u91cd\u53e0\u6ce8\u5165", overlap > 0.2)
    
    # Low overlap
    qk2 = CriticEvaluator.extract_keywords("\u5b89\u5168\u5e93\u5b58\u8ba1\u7b97\u516c\u5f0f")
    gk2 = CriticEvaluator.extract_keywords("\u4f9b\u5e94\u5546\u8bc4\u7ea7\u6807\u51c6 \u8d28\u91cf\u5408\u683c\u738740%")
    overlap2 = len(qk2 & gk2) / max(len(qk2), 1)
    check("Graph \u4f4e\u91cd\u53e0\u8fc7\u6ee4", overlap2 <= 0.2)
except Exception as e:
    check("Graph Critic", False, str(e))

# ----------------------------------------------------------
# 7. Milvus Connection (optional)
# ----------------------------------------------------------
print("\n[7] Milvus Connection (optional)")
try:
    from app.core.milvus_client import milvus_manager
    milvus_manager.connect()
    milvus_manager.create_collection()  # Load the collection
    count = milvus_manager.collection.num_entities
    check("Milvus \u8fde\u63a5", True)
    check(f"Milvus chunks ({count})", count > 0)
except Exception as e:
    skip("Milvus \u8fde\u63a5", f"\u9700\u8981 Docker: {e}")

# ----------------------------------------------------------
# 8. Redis Connection (optional)
# ----------------------------------------------------------
print("\n[8] Redis Connection (optional)")
try:
    from app.core.redis_client import redis_manager
    import asyncio
    asyncio.get_event_loop().run_until_complete(redis_manager.connect())
    check("Redis \u8fde\u63a5", True)
except Exception as e:
    skip("Redis \u8fde\u63a5", f"\u9700\u8981 Docker: {e}")

# ----------------------------------------------------------
# 9. Neo4j Connection (optional)
# ----------------------------------------------------------
print("\n[9] Neo4j Connection (optional)")
try:
    import asyncio
    from app.core.neo4j_client import neo4j_client
    if not neo4j_client.is_connected:
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(neo4j_client.connect())
        except RuntimeError:
            asyncio.run(neo4j_client.connect())
    if neo4j_client.is_connected:
        check("Neo4j \u8fde\u63a5", True)
    else:
        skip("Neo4j \u8fde\u63a5", "\u672a\u8fde\u63a5\uff08\u9700\u8981 Docker\uff09")
except Exception as e:
    skip("Neo4j \u8fde\u63a5", f"\u8fde\u63a5\u5931\u8d25: {e}")

# ----------------------------------------------------------
# 10. Reranker Model (optional)
# ----------------------------------------------------------
print("\n[10] Reranker Model (optional)")
try:
    from app.core.rag_engine import RerankerEngine
    r = RerankerEngine()
    r.init()
    if r._model is not None:
        check("Reranker \u52a0\u8f7d", True)
    else:
        skip("Reranker \u52a0\u8f7d", "\u6a21\u578b\u672a\u52a0\u8f7d\uff08\u53ef\u80fd\u9700\u8981\u4e0b\u8f7d\uff09")
except Exception as e:
    skip("Reranker \u52a0\u8f7d", f"\u52a0\u8f7d\u5931\u8d25: {e}")

# ----------------------------------------------------------
# Summary
# ----------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok is True)
failed = sum(1 for _, ok, _ in results if ok is False)
skipped = sum(1 for _, ok, _ in results if ok is None)
total = len(results)

print(f"Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
print("=" * 60)

if failed > 0:
    print("\n\u26a0\ufe0f Failed checks:")
    for name, ok, detail in results:
        if ok is False:
            print(f"  - {name}: {detail}")
    sys.exit(1)
else:
    print("\n\u2705 All checks passed!")
    sys.exit(0)
