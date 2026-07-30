# -*- coding: utf-8 -*-
"""知识库冲突治理的索引刷新脚本。

两种模式：
  --audit   只审计：全量拉取 Milvus chunks，列出仍含冲突关键词的 chunk（按 source 分组）
  （默认）  执行刷新：删除指定 doc 的旧 chunks，重灌 knowledge/ 下修改后的文档，
            并删除已废弃文档（供应链物料编码规范.md）及 uploads 副本的冲突 doc

用法：
  cd backend
  venv\\Scripts\\python.exe scripts\\reindex_changed_docs.py --audit
  venv\\Scripts\\python.exe scripts\\reindex_changed_docs.py
"""
import argparse
import hashlib
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "..", "knowledge")

# 本次治理修改过、需重灌的文档
CHANGED_DOCS = [
    "物料编码规则说明.md",
    "质检标准与不合格品处理.md",
    "库存管理ABC分类法.md",
    "库存管理制度.md",
]
# 已废弃、只删不灌的文档
REMOVED_DOCS = ["供应链物料编码规范.md"]

# 冲突残留关键词（治理后库里不应再出现）
CONFLICT_KEYWORDS = [
    "XX-XXX-XX-XXXX",          # 废弃的四段式编码格式
    "大类+中类+小类+流水号",     # 废弃结构描述
    "抽检比例不低于 30%",        # 旧 B 类抽检值
    "抽检比例不低于30%",
    "月度盘点，安全库存适当冗余",  # 旧 B 类盘点周期
    "季度盘点，批量采购",         # 旧 C 类盘点周期
]

DEPT_GROUPS = {
    "purchase":   ["admin", "purchase", "finance"],
    "warehouse":  ["admin", "warehouse", "production", "logistics"],
    "quality":    ["admin", "quality"],
    "production": ["admin", "production", "warehouse"],
    "finance":    ["admin", "finance"],
    "logistics":  ["admin", "warehouse", "logistics"],
    "admin":      ["admin"],
}


def _fetch_all_chunks(milvus_manager):
    """分批拉取全库 chunk 的 (doc_id, chunk_id, source, content)。"""
    c = milvus_manager.collection
    c.load()
    rows, offset, batch = [], 0, 1000
    while True:
        got = c.query(expr="id > 0", output_fields=["doc_id", "chunk_id", "source", "content"],
                      limit=batch, offset=offset)
        if not got:
            break
        rows.extend(got)
        offset += batch
    return rows


def audit(milvus_manager):
    rows = _fetch_all_chunks(milvus_manager)
    print(f"全库 {len(rows)} chunks，扫描冲突关键词...")
    hits = defaultdict(list)
    for r in rows:
        content = r.get("content", "")
        for kw in CONFLICT_KEYWORDS:
            if kw.replace(" ", "") in content.replace(" ", ""):
                hits[(r["doc_id"], r.get("source", ""))].append(kw)
                break
    if not hits:
        print("未发现冲突残留。")
        return
    print(f"\n发现 {len(hits)} 个含冲突内容的 (doc_id, source)：")
    for (doc_id, source), kws in sorted(hits.items(), key=lambda x: x[0][1]):
        print(f"  doc_id={doc_id}  source={source}  命中={sorted(set(kws))}")


def refresh(milvus_manager):
    from app.core.rag_engine import rag_engine
    from app.api.knowledge import _chunk_text
    from app.config import get_settings
    settings = get_settings()

    # 1) 全量拉取，按 source 实查待处理文档的真实 doc_id（库内 doc_id 位数与入库脚本有关，不可按文件名 hash 猜）
    rows = _fetch_all_chunks(milvus_manager)
    target_files = set(CHANGED_DOCS + REMOVED_DOCS)
    to_delete = {}   # doc_id -> source
    doc_id_by_source = {}
    for r in rows:
        src, did = r.get("source", ""), r["doc_id"]
        if src in target_files:
            to_delete[did] = src
            doc_id_by_source[src] = did
        elif any(kw.replace(" ", "") in r.get("content", "").replace(" ", "") for kw in CONFLICT_KEYWORDS):
            to_delete[did] = src  # 兼平历史副本残留

    # 2) 删除旧 chunks
    for doc_id, source in to_delete.items():
        milvus_manager.delete_by_doc_id(doc_id)
        print(f"  删除 doc_id={doc_id} ({source})")

    # 3) 重灌修改后的文档（复用库内原 doc_id，无则用 md5[:12]）
    for fname in CHANGED_DOCS:
        path = os.path.join(KNOWLEDGE_DIR, fname)
        text = open(path, encoding="utf-8").read()
        chunks = _chunk_text(text, chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)
        doc_id = doc_id_by_source.get(fname) or hashlib.md5(fname.encode()).hexdigest()[:12]
        records = [{"chunk_id": f"{doc_id}-chunk-{j:04d}", "content": c.get("content", ""),
                    "section_title": c.get("section_title", ""), "source": fname}
                   for j, c in enumerate(chunks)]
        parts = fname.replace(".md", "").split("-")
        dept = parts[1] if len(parts) >= 2 else "admin"
        rag_engine.index_document(doc_id, records, security_group=DEPT_GROUPS.get(dept, ["admin"]))
        print(f"  重灌 {fname}: {len(records)} chunks")

    # 4) 语义缓存失效（版本号 INCR，旧条目惰性清理）
    try:
        import asyncio
        from app.core.semantic_cache import semantic_cache
        asyncio.run(semantic_cache.invalidate())
        print("  语义缓存已失效 (INCR version)")
    except Exception as e:
        print(f"  [WARN] 语义缓存失效失败（不影响评测直连链路）: {type(e).__name__}: {e}")

    print("\n刷新完成，建议再跑一次 --audit 验证无残留。")


def main():
    ap = argparse.ArgumentParser(description="知识库冲突治理索引刷新")
    ap.add_argument("--audit", action="store_true", help="只审计不修改")
    args = ap.parse_args()

    from app.core.milvus_client import milvus_manager
    milvus_manager.connect()
    milvus_manager.create_collection()
    if args.audit:
        audit(milvus_manager)
    else:
        refresh(milvus_manager)
    milvus_manager.disconnect()


if __name__ == "__main__":
    main()
