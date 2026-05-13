"""演示全链路验证脚本 — 按 OpenSpec REQ-4 逐条测试

用法：
  python scripts/verify_demo.py

测试范围：后端 API 可用性 → 登录 → 工具列表 → 权限 → 健康检查
不测试：SSE 流式（需要浏览器验证）、CLIP 图片上传
"""
import json, sys, httpx, time

API = "http://localhost:8001/api/v1"
PASS, FAIL = 0, 0
results = []

def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        results.append(f"  ✅ {name}")
    else:
        FAIL += 1
        results.append(f"  ❌ {name} — {detail}")

def test(label):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")

def login(user, pwd):
    try:
        resp = httpx.post(f"{API}/auth/login", json={"username": user, "password": pwd}, timeout=10)
        return resp
    except Exception as e:
        return None

# ============================================================
test("1. 后端基础可用性")
# ============================================================
try:
    resp = httpx.get("http://localhost:8001/health", timeout=5)
    check("GET /health 返回 200", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        data = resp.json()
        check("health 包含 status", "status" in data)
        check("health 包含 embedding_model", "embedding_model" in data)
        check("health 包含 knowledge_docs_count", "knowledge_docs_count" in data)
        print(f"     embedding_model = {data.get('embedding_model', '?')}")
        print(f"     knowledge_docs_count = {data.get('knowledge_docs_count', '?')}")
        print(f"     reranker_enabled = {data.get('reranker_enabled', '?')}")
except Exception as e:
    check("GET /health 可达", False, str(e))

try:
    resp = httpx.get("http://localhost:8001/docs", timeout=5)
    check("GET /docs 可访问", resp.status_code == 200, str(resp.status_code))
except:
    check("GET /docs 可访问", False, "无法连接")

# ============================================================
test("2. 认证系统")
# ============================================================
for user, pwd, dept in [
    ("admin", "admin123", "admin"),
    ("purchase", "123456", "purchase"),
    ("finance", "123456", "finance"),
    ("quality", "123456", "quality"),
]:
    resp = login(user, pwd)
    if resp and resp.status_code == 200:
        data = resp.json()
        has_token = "token" in data
        role = data.get("user", {}).get("role", "?")
        check(f"登录 {user}/{dept}", has_token and role == dept,
              f"token={'yes' if has_token else 'no'}, role={role}")
        # 保存 admin token 后续使用
        if dept == "admin":
            admin_token = data["token"]
        if dept == "purchase":
            purchase_token = data["token"]
        if dept == "finance":
            finance_token = data["token"]
    else:
        code = resp.status_code if resp else "timeout"
        check(f"登录 {user}", False, str(code))

# ============================================================
test("3. 知识库 API")
# ============================================================
try:
    resp = httpx.get(f"{API}/knowledge/list",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
    check("GET /knowledge/list (admin)", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        docs = resp.json()
        check(f"文档列表非空", len(docs) > 0, f"共 {len(docs)} 篇")
except Exception as e:
    check("GET /knowledge/list", False, str(e))

# ============================================================
test("4. 工具列表与权限")
# ============================================================
try:
    resp = httpx.get(f"{API}/tools/list",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
    check("GET /tools/list (admin)", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        tools = resp.json()
        tool_names = [t["name"] for t in tools]
        check("admin 有 6 个工具", len(tools) >= 6, f"实际 {len(tools)}")
        check("包含 query_inventory", "query_inventory" in tool_names)
        check("包含 query_order", "query_order" in tool_names)
        check("包含 create_ticket", "create_ticket" in tool_names)
        check("包含 query_supplier", "query_supplier" in tool_names)
except Exception as e:
    check("GET /tools/list", False, str(e))

# 权限测试
try:
    resp = httpx.get(f"{API}/tools/list",
                     headers={"Authorization": f"Bearer {finance_token}"}, timeout=10)
    if resp.status_code == 200:
        tools = resp.json()
        tool_names = [t["name"] for t in tools]
        check("finance 无 query_inventory", "query_inventory" not in tool_names,
              f"tools={tool_names}")
        check("finance 有 get_datetime", "get_datetime" in tool_names)
except Exception as e:
    check("GET /tools/list (finance)", False, str(e))

# ============================================================
test("5. 工具调用 — 非流式")
# ============================================================
try:
    resp = httpx.post(f"{API}/chat/completions",
                      json={"query": "MAT-001 库存多少", "stream": False},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("chat/completions 返回 200", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        data = resp.json()
        has_answer = bool(data.get("answer"))
        intent = data.get("intent", "?")
        check(f"返回有 answer (intent={intent})", has_answer)
except Exception as e:
    check("chat/completions", False, str(e))

# ============================================================
test("6. RAG 检索 — 非流式")
# ============================================================
try:
    resp = httpx.post(f"{API}/chat/completions",
                      json={"query": "新供应商准入需要什么资质", "stream": False},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("RAG completions 返回 200", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        data = resp.json()
        has_answer = bool(data.get("answer"))
        has_sources = bool(data.get("sources"))
        check("RAG 返回有 answer", has_answer)
        check("RAG 返回有 sources", has_sources)
        if has_answer:
            print(f"     answer 前 100 字: {data['answer'][:100]}...")
except Exception as e:
    check("RAG completions", False, str(e))

# ============================================================
test("7. 权限拒绝 — finance 调 query_inventory")
# ============================================================
# 这个测试需要 SSE，用 completions 可能不会触发工具调用
# 用 tool/call 端点直接测试
try:
    resp = httpx.post(f"{API}/tools/call",
                      json={"tool_name": "query_inventory", "tool_input": {"material_code": "MAT-001"}},
                      headers={"Authorization": f"Bearer {finance_token}"}, timeout=10)
    check("finance tool/call query_inventory 被拒", resp.status_code == 403,
          f"status={resp.status_code}, body={resp.text[:80]}")
except Exception as e:
    check("finance tool/call", False, str(e))

try:
    resp = httpx.post(f"{API}/tools/call",
                      json={"tool_name": "query_inventory", "tool_input": {"material_code": "MAT-001"}},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=10)
    check("purchase tool/call query_inventory 成功", resp.status_code == 200,
          f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        has_result = bool(data.get("result"))
        check("purchase 返回库存数据", has_result)
        if has_result:
            print(f"     result 前 100 字: {str(data['result'])[:100]}")
except Exception as e:
    check("purchase tool/call", False, str(e))

# ============================================================
test("8. 健康检查 — 全链路")
# ============================================================
try:
    resp = httpx.get("http://localhost:8001/health", timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        print(f"     status: {data.get('status', '?')}")
        print(f"     embedding_model: {data.get('embedding_model', '?')}")
        print(f"     knowledge_docs_count: {data.get('knowledge_docs_count', '?')}")
        print(f"     reranker_enabled: {data.get('reranker_enabled', '?')}")
        print(f"     agent_type: {data.get('agent_type', '?')}")
except:
    pass

# ============================================================
print(f"\n{'='*50}")
print(f"  结果: {PASS} 通过 / {FAIL} 失败 / {PASS+FAIL} 总计")
print(f"{'='*50}")

if FAIL > 0:
    print("\n失败项目：")
    for r in results:
        if "❌" in r:
            print(r)

sys.exit(0 if FAIL == 0 else 1)
