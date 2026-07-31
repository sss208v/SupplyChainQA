# -*- coding: utf-8 -*-
"""Upload all MD files from knowledge/ directory to Milvus.

权限分隔：根据文件名前缀（SC-{dept}-xxx.md）分配 security_group，
实现行级 RBAC——不同部门的用户只能看到自己部门的文档。
"""
import os, sys, json, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.api.knowledge import _chunk_text
from app.config import get_settings

settings = get_settings()
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "..", "knowledge")

# 部门 → 可见角色列表
DEPT_GROUPS = {
    "purchase":   ["admin", "purchase", "finance"],
    "warehouse":  ["admin", "warehouse", "production", "logistics"],
    "quality":    ["admin", "quality"],
    "production": ["admin", "production", "warehouse"],
    "finance":    ["admin", "finance"],
    "logistics":  ["admin", "warehouse", "logistics"],
    "admin":      ["admin"],
}

print(f"Knowledge dir: {KNOWLEDGE_DIR}")
print(f"Chunk config: size={settings.CHUNK_SIZE}, overlap={settings.CHUNK_OVERLAP}")

# Find all MD files
md_files = []
for root, dirs, files in os.walk(KNOWLEDGE_DIR):
    for f in files:
        if f.endswith(".md") and f != "README.md":
            md_files.append(os.path.join(root, f))

print(f"Found {len(md_files)} MD files")

# Connect to Milvus
milvus_manager.connect()

total_chunks = 0
for i, path in enumerate(md_files):
    fname = os.path.basename(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            print(f"  [{i+1}/{len(md_files)}] SKIP empty: {fname}")
            continue
        
        # Chunk the document using settings chunk_size/chunk_overlap
        chunks = _chunk_text(text, chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)
        
        # Generate ID and index each chunk
        import hashlib
        from datetime import datetime
        doc_id = hashlib.md5(fname.encode()).hexdigest()[:16]
        
        chunk_records = []
        for j, c in enumerate(chunks):
            chunk_id = f"{doc_id}-chunk-{j:04d}"
            chunk_records.append({
                "chunk_id": chunk_id,
                "content": c.get("content", ""),
                "section_title": c.get("section_title", ""),
                "source": fname,
            })
        
        # 从文件名解析部门: SC-purchase-253.md → purchase
        parts = fname.replace(".md", "").split("-")
        dept = parts[1] if len(parts) >= 2 else "admin"
        sec_groups = DEPT_GROUPS.get(dept, ["admin"])

        rag_engine.index_document(doc_id, chunk_records, security_group=sec_groups)
        total_chunks += len(chunks)
        print(f"  [{i+1}/{len(md_files)}] {fname}: {len(chunks)} chunks → {sec_groups}")
    except Exception as e:
        print(f"  [{i+1}/{len(md_files)}] ERROR {fname}: {e}")

print(f"\nDone! Total: {total_chunks} chunks from {len(md_files)} files")
milvus_manager.disconnect()
