"""知识库批量上传脚本 — 将 knowledge/SC-*.md 全部索引到 Milvus

用法：后端启动后执行
  cd backend
  .\venv\Scripts\python.exe scripts/upload_knowledge_base.py

依赖：后端 API 在 localhost:8001 运行，admin/admin123 可用
"""
import os, sys, json, httpx, glob, time

API = "http://localhost:8001/api/v1"
KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge")

# 每个部门的 security_group 映射
DEPT_GROUPS = {
    "purchase":   "admin,purchase,finance",
    "warehouse":  "admin,warehouse,production,logistics",
    "quality":    "admin,quality",
    "production": "admin,production,warehouse",
    "finance":    "admin,finance",
    "logistics":  "admin,warehouse,logistics",
    "admin":      "admin",
}

def login():
    resp = httpx.post(f"{API}/auth/login", json={"username": "admin", "password": "admin123"})
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败: {resp.status_code} {resp.text}")
    token = resp.json()["token"]
    print(f"✅ 登录成功 (admin)")
    return token

def upload_file(filepath, token, dept):
    fname = os.path.basename(filepath)
    groups = DEPT_GROUPS.get(dept, "admin")
    
    with open(filepath, "rb") as f:
        files = {
            "file": (fname, f, "text/markdown"),
            "security_group": (None, groups),
        }
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.post(f"{API}/knowledge/upload", files=files, headers=headers)
    
    if resp.status_code == 200:
        data = resp.json()
        chunks = data.get("chunks_count", data.get("chunk_count", "?"))
        print(f"  ✅ {fname} → {chunks} chunks")
        return True
    else:
        print(f"  ❌ {fname} → {resp.status_code}: {resp.text[:100]}")
        return False

def main():
    print("=" * 60)
    print("Supply Chain QA 知识库批量上传")
    print("=" * 60)
    
    # 1. 登录
    try:
        token = login()
    except Exception as e:
        print(f"❌ 无法登录: {e}")
        print("请确认后端已启动: uvicorn app.main:app --port 8001")
        sys.exit(1)
    
    # 2. 收集文件（SC-*.md）
    files = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "SC-*.md")))
    print(f"\n📂 找到 {len(files)} 个 SC-*.md 文件")
    
    # 3. 批量上传
    success, fail = 0, 0
    for filepath in files:
        fname = os.path.basename(filepath)
        # 从文件名提取部门: SC-purchase-253.md → purchase
        parts = fname.replace(".md", "").split("-")
        dept = parts[1] if len(parts) >= 2 else "admin"
        
        if upload_file(filepath, token, dept):
            success += 1
        else:
            fail += 1
        
        time.sleep(0.3)  # 避免打爆后端
    
    # 4. 检查结果
    print(f"\n📊 上传完成: {success} 成功 / {fail} 失败")
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.get(f"{API}/knowledge/list", headers=headers)
        if resp.status_code == 200:
            total = len(resp.json())
            print(f"📚 知识库文档总数: {total}")
    except:
        pass
    
    # 5. 检查 health
    try:
        resp = httpx.get("http://localhost:8001/health")
        if resp.status_code == 200:
            data = resp.json()
            count = data.get("knowledge_docs_count", "?")
            print(f"💾 Milvus 向量总数: {count}")
    except:
        pass

if __name__ == "__main__":
    main()
