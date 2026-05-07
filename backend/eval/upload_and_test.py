"""上传知识库并测试检索 - 带结果输出到文件"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine

output_lines = []

def log(msg):
    print(msg)
    output_lines.append(msg)

# 1. Connect Milvus
log("Connecting Milvus...")
milvus_manager.connect()
log("  OK: Milvus connected")

# 2. Check if data already exists
stats = milvus_manager.get_collection_stats()
existing_chunks = stats.get("num_entities", 0)
log(f"  Current chunks in Milvus: {existing_chunks}")

# 3. Upload only if empty
if existing_chunks == 0:
    file_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "企业IT支持知识库.md")
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    log(f"File size: {len(content)} chars")

    chunk_size = 512
    chunk_overlap = 64
    chunks = []
    doc_id = "eval-kb-001"

    for i in range(0, len(content), chunk_size - chunk_overlap):
        chunk_text = content[i : i + chunk_size]
        if not chunk_text.strip():
            continue
        chunks.append({
            "chunk_id": f"{doc_id}-chunk-{len(chunks)}",
            "content": chunk_text,
            "source": "企业IT支持知识库.md",
            "page_num": i // chunk_size + 1,
        })

    log(f"Chunk count: {len(chunks)}")

    log("Indexing (Embedding + Milvus)...")
    start = time.time()
    result = rag_engine.index_document(doc_id, chunks)
    elapsed = time.time() - start
    log(f"  OK: Indexing done in {elapsed:.1f}s, result: {result}")
else:
    log("  Data already exists, skipping upload")

# 4. Test search
log("\nTesting search...")
test_queries = [
    "VPN服务器地址是什么",
    "打印机IP地址",
    "新员工入职IT",
    "密码策略",
]

for q in test_queries:
    try:
        test_result = rag_engine.search(q, top_k=3)
        results = test_result.get("results", [])
        log(f"\n  Query: {q}")
        log(f"  Results: {len(results)}")
        for i, r in enumerate(results):
            score = r.get("rerank_score", r.get("score", 0))
            log(f"    [{i+1}] score={score:.4f} | {r.get('content', '')[:80]}...")
    except Exception as e:
        log(f"  Search error: {type(e).__name__}: {e}")

# Save output
output_path = os.path.join(os.path.dirname(__file__), "upload_result.txt")
with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(output_lines))
log(f"\nOutput saved to: {output_path}")
