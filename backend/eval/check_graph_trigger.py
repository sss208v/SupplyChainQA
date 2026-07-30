# -*- coding: utf-8 -*-
"""图谱触发率验收（P1-1）：逐题调 rag_engine.search，直接检查返回结果中
是否存在 source=neo4j_graph 的伪 chunk（不依赖日志，日志在评测进程默认静默）。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\check_graph_trigger.py [--dataset eval\\eval_set_graph.json]
"""
import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 评测进程默认无 logging handler，显式打开 INFO 以便看到 [GraphRAG] 链路日志
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("neo4j").setLevel(logging.WARNING)

from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.core.neo4j_client import neo4j_client
from app.config import get_settings
from eval.eval_utils import rebuild_bm25_from_milvus

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
settings = get_settings()


def main():
    ap = argparse.ArgumentParser(description="图谱伪 chunk 注入触发率验收")
    ap.add_argument("--dataset", default=os.path.join(EVAL_DIR, "eval_set_graph.json"))
    args = ap.parse_args()

    ok = asyncio.run(neo4j_client.connect())
    print(f"Neo4j connect: {ok}")

    milvus_manager.connect()
    milvus_manager.create_collection()
    rebuild_bm25_from_milvus(rag_engine, milvus_manager)

    items = json.load(open(args.dataset, encoding="utf-8"))
    hits = 0
    for it in items:
        q = it["question"]
        r = rag_engine.search(q, top_k=settings.RERANK_TOP_K)
        graph_chunks = [d for d in r.get("results", [])
                        if d.get("retrieval_source") == "neo4j_graph"
                        or str(d.get("source", "")).startswith("neo4j_graph")]
        hit = bool(graph_chunks)
        hits += hit
        mark = "HIT " if hit else "MISS"
        print(f"[{mark}] {q}")
        if hit:
            print(f"       graph chunk: {graph_chunks[0].get('content', '')[:100]}")
    print(f"\n注入率: {hits}/{len(items)} = {hits / max(len(items), 1):.0%}")


if __name__ == "__main__":
    main()
