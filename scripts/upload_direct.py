"""直连 Milvus 上传知识库（仅加载 embedding 模型，不启动 HTTP 服务）

用法：
  cd backend
  .\venv\Scripts\python.exe scripts/upload_direct.py
"""
import os, sys, glob, time, hashlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

print("Loading modules...")
from app.core.milvus_client import milvus_manager
from app.config import get_settings

settings = get_settings()
KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "knowledge")

DEPT_GROUPS = {
    "purchase":   ["admin", "purchase", "finance"],
    "warehouse":  ["admin", "warehouse", "production", "logistics"],
    "quality":    ["admin", "quality"],
    "production": ["admin", "production", "warehouse"],
    "finance":    ["admin", "finance"],
    "logistics":  ["admin", "warehouse", "logistics"],
    "admin":      ["admin"],
}

def load_embedding_model():
    print("Loading embedding model...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(settings.EMBEDDING_MODEL, device=settings.EMBEDDING_DEVICE)
    print(f"  [OK] {settings.EMBEDDING_MODEL} loaded")
    return model

def chunk_text(text, size=1000, overlap=200):
    paragraphs = text.split('\n\n')
    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) < size:
            current += p + "\n\n"
        else:
            if current.strip():
                chunks.append(current.strip())
            current = p + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks

def main():
    print("=" * 60)
    print("Supply Chain QA Knowledge Base Direct Upload")
    print("=" * 60)
    
    # 1. Load embedding model
    model = load_embedding_model()
    
    # 2. Connect Milvus
    print("\nConnecting Milvus...")
    milvus_manager.connect()
    count = milvus_manager.get_count()
    print(f"  [OK] Connected, current chunks: {count}")
    
    # 3. Collect new files
    files = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "SC-*.md")))
    print(f"\nFound {len(files)} new documents")
    
    # 4. Upload
    total_chunks = 0
    for filepath in files:
        fname = os.path.basename(filepath)
        parts = fname.replace(".md", "").split("-")
        dept = parts[1] if len(parts) >= 2 else "admin"
        security_group = DEPT_GROUPS.get(dept, ["admin"])
        
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
        
        if len(text) < 100:
            continue
        
        chunks = chunk_text(text)
        if not chunks:
            continue
        
        doc_id = hashlib.md5(fname.encode()).hexdigest()[:12]
        
        for i, chunk_content in enumerate(chunks):
            chunk_id = f"{doc_id}_{i:03d}"
            embedding = model.encode(chunk_content).tolist()
            
            try:
                milvus_manager.insert(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    content=chunk_content[:60000],
                    embedding=embedding,
                    source=fname,
                    security_group=security_group,
                )
            except Exception as e:
                print(f"    [!!] {chunk_id}: {e}")
                break
        
        print(f"  [OK] {fname} -> {len(chunks)} chunks [{dept}]")
        total_chunks += len(chunks)
        time.sleep(0.05)
    
    # 5. Flush
    milvus_manager.collection.flush()
    time.sleep(2)
    final_count = milvus_manager.get_count()
    
    print(f"\n[DONE] +{total_chunks} chunks, Milvus total: {final_count}")

if __name__ == "__main__":
    main()
