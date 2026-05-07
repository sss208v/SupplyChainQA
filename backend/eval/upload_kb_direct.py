"""直接调用 SmartQA 内部方法上传知识库（绕过 HTTP API 超时问题）"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine

# 1. 连接 Milvus
print("连接 Milvus...")
milvus_manager.connect()
print("  ✅ Milvus 已连接")

# 2. 读取知识库文件
file_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "企业IT支持知识库.md")
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()
print(f"文件大小: {len(content)} 字符")

# 3. 切片
chunk_size = 512
chunk_overlap = 64
chunks = []
doc_id = "eval-kb-001"

for i in range(0, len(content), chunk_size - chunk_overlap):
    chunk_text = content[i : i + chunk_size]
    if not chunk_text.strip():
        continue
    chunks.append(
        {
            "chunk_id": f"{doc_id}-chunk-{len(chunks)}",
            "content": chunk_text,
            "source": "企业IT支持知识库.md",
            "page_num": i // chunk_size + 1,
        }
    )

print(f"切片数量: {len(chunks)}")

# 4. 索引到 Milvus + BM25
print("开始索引（生成 Embedding + 写入 Milvus）...")
start = time.time()
result = rag_engine.index_document(doc_id, chunks)
elapsed = time.time() - start
print(f"  ✅ 索引完成，耗时 {elapsed:.1f}s")
print(f"  结果: {result}")

# 5. 验证
stats = milvus_manager.get_collection_stats()
print(f"\n知识库统计: {stats}")

# 6. 测试检索
print("\n测试检索...")
test_result = rag_engine.search("VPN服务器地址是什么", top_k=3)
for i, r in enumerate(test_result.get("results", [])):
    print(f"  [{i+1}] score={r.get('rerank_score', r.get('score', 0)):.4f} | {r.get('content', '')[:80]}...")
