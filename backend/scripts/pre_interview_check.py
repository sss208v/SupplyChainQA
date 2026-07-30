# -*- coding: utf-8 -*-
"""面试前一键验证脚本"""
import sys, os, asyncio
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    checks = []
    
    # 1. Docker 服务
    try:
        from app.core.milvus_client import milvus_manager
        milvus_manager.connect()
        milvus_manager.create_collection()
        count = milvus_manager.collection.num_entities
        checks.append(("Milvus", True, f"{count} chunks"))
    except Exception as e:
        checks.append(("Milvus", False, str(e)))
    
    # 2. Redis
    try:
        from app.core.redis_client import redis_manager
        await redis_manager.connect()
        checks.append(("Redis", True, "Connected"))
    except Exception as e:
        checks.append(("Redis", False, str(e)))
    
    # 3. Neo4j
    try:
        from app.core.neo4j_client import neo4j_client
        if not neo4j_client.is_connected:
            await neo4j_client.connect()
        checks.append(("Neo4j", True, "Connected"))
    except Exception as e:
        checks.append(("Neo4j", False, str(e)))
    
    # 4. RAG Agent
    try:
        from app.agents.rag import rag_agent
        checks.append(("RAG Agent", True, "Imported"))
    except Exception as e:
        checks.append(("RAG Agent", False, str(e)))
    
    # 5. Agentic RAG 组件
    try:
        from app.core.rag_engine import CriticEvaluator, QueryRewriter
        from app.core.llm_relevance import get_self_rag
        from app.core.query_analyzer import query_analyzer
        checks.append(("Agentic RAG", True, "All components imported"))
    except Exception as e:
        checks.append(("Agentic RAG", False, str(e)))
    
    # 6. Config
    try:
        from app.config import get_settings
        s = get_settings()
        checks.append(("Config", True, f"CRAG={s.CRAG_ENABLED}, LLMRelevance={s.LLM_RELEVANCE_ENABLED}"))
    except Exception as e:
        checks.append(("Config", False, str(e)))
    
    # Print results
    print("=" * 50)
    print("Pre-Interview Verification")
    print("=" * 50)
    passed = 0
    for name, ok, detail in checks:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}: {detail}")
        if ok:
            passed += 1
    
    print(f"\nResult: {passed}/{len(checks)} passed")
    
    if passed == len(checks):
        print("\n🎉 All checks passed! Ready for interview.")
    else:
        print("\n⚠️ Some checks failed. Please fix before interview.")

if __name__ == "__main__":
    asyncio.run(main())
