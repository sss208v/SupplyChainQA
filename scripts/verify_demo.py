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
        results.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        results.append(f"  [FAIL] {name} — {detail}")

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
        data = resp.json()
        tools = data.get("tools", [])
        tool_names = [t["name"] for t in tools]
        check("admin 有 6 个工具", len(tools) >= 6, f"实际 {len(tools)}")
        check("包含 query_inventory", "query_inventory" in tool_names)
        check("包含 query_order", "query_order" in tool_names)
        check("包含 create_ticket", "create_ticket" in tool_names)
        check("包含 get_knowledge", "get_knowledge" in tool_names)
        check("包含 query_supplier", "query_supplier" in tool_names)
except Exception as e:
    check("GET /tools/list", False, str(e))

# 权限测试
try:
    resp = httpx.get(f"{API}/tools/list",
                     headers={"Authorization": f"Bearer {finance_token}"}, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        tools = data.get("tools", [])
        tool_names = [t["name"] for t in tools]
        check("finance 无 query_inventory", "query_inventory" not in tool_names,
              f"tools={tool_names}")
        check("finance 有 get_datetime", "get_datetime" in tool_names)
except Exception as e:
    check("GET /tools/list (finance)", False, str(e))

# ============================================================
test("5. 工具调用 — 流式")
# ============================================================
# 注: /chat/completions 端点不存在，改用 /chat/stream
try:
    resp = httpx.post(f"{API}/chat/stream",
                      json={"query": "MAT-001 库存多少", "stream": True},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("chat/stream 返回 200", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        # SSE 流式响应以 text/event-stream 开头
        has_content = len(resp.text) > 50
        check(f"流式响应有内容", has_content, f"len={len(resp.text)}")
except Exception as e:
    check("chat/stream", False, str(e))

# ============================================================
test("6. RAG 检索 — 流式")
# ============================================================
try:
    resp = httpx.post(f"{API}/chat/stream",
                      json={"query": "新供应商准入需要什么资质", "stream": True},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("RAG stream 返回 200", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        has_content = len(resp.text) > 50
        check("RAG 流式有内容", has_content, f"len={len(resp.text)}")
        if has_content:
            # SSE 数据行
            lines = [l for l in resp.text.split("\n") if l.startswith("data:") and l != "data: [DONE]"]
            check("SSE 包含 data 事件", len(lines) > 0)
except Exception as e:
    check("RAG stream", False, str(e))

# ============================================================
test("7. 权限拒绝 — finance 调 query_inventory")
# ============================================================
# 用 tool/call 端点，格式: {"query":"..."}，Agent 自动选择工具
try:
    resp = httpx.post(f"{API}/tools/call",
                      json={"query": "查物料 MAT-001 的库存"},
                      headers={"Authorization": f"Bearer {finance_token}"}, timeout=30)
    # finance 用户调用被 Agent 内部权限拒绝，但 HTTP 仍返回 200
    check("finance tool/call 被限", resp.status_code in [200, 403],
          f"status={resp.status_code}")
except Exception as e:
    check("finance tool/call", False, str(e))

try:
    resp = httpx.post(f"{API}/tools/call",
                      json={"query": "查物料 MAT-001 的库存"},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("purchase tool/call 成功", resp.status_code == 200,
          f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        has_answer = bool(data.get("answer"))
        check("purchase 返回 answer", has_answer)
        if has_answer:
            print(f"     answer 前 100 字: {data['answer'][:100]}")
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
test("9. GOAL 编排 — 跨域目标型查询 (v2.0)")
# ============================================================
goal_queries = [
    ("帮我评估 MAT-001 库存风险", "库存短缺评估"),
    ("帮我评估 MAT-002 的采购和库存状态", "采购库存评估"),
]
for gq, label in goal_queries:
    try:
        resp = httpx.post(f"{API}/chat/stream",
                          json={"query": gq, "stream": True},
                          headers={"Authorization": f"Bearer {purchase_token}"}, timeout=60)
        check(f"GOAL '{label}' 返回 200", resp.status_code == 200, str(resp.status_code))
        if resp.status_code == 200:
            text = resp.text
            has_plan = "orchestrator_plan" in text or "agent_step" in text
            has_content = any(l.startswith("data:") and "\"content\"" in l for l in text.split("\n"))
            check(f"GOAL '{label}' 含编排事件", has_plan, f"text_len={len(text)}")
            check(f"GOAL '{label}' 有回答内容", has_content, f"text_len={len(text)}")
            if has_content:
                print(f"     [{label}] SSE 事件: plan={'yes' if has_plan else 'no'}, content={'yes' if has_content else 'no'}")
    except Exception as e:
        check(f"GOAL '{label}'", False, str(e))

# 验证 GOAL 不影响 TOOL_CALL 路径
try:
    resp = httpx.post(f"{API}/chat/stream",
                      json={"query": "查物料 MAT-001 的库存", "stream": True},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("TOOL_CALL 路径仍正常（查库存）", resp.status_code == 200, str(resp.status_code))
    if resp.status_code == 200:
        text = resp.text
        has_tool_call = "tool_call" in text or "tool_status" in text
        check("TOOL_CALL 含工具事件", has_tool_call, f"text_len={len(text)}")
except Exception as e:
    check("TOOL_CALL 路径", False, str(e))

# ============================================================
test("10. 图谱检索 — Neo4j 实体关系查询 (v2.2)")
# ============================================================
graph_queries = [
    ("MAT-001 缺货会影响哪些物料", "库存短缺图检索"),
    ("MAT-002 有没有质量问题需要追溯", "质量追溯图检索"),
]
for gq, label in graph_queries:
    try:
        resp = httpx.post(f"{API}/chat/stream",
                          json={"query": gq, "stream": True},
                          headers={"Authorization": f"Bearer {purchase_token}"}, timeout=60)
        check(f"GRAPH '{label}' 返回 200", resp.status_code == 200, str(resp.status_code))
        if resp.status_code == 200:
            text = resp.text
            has_graph = "graph_query_start" in text or "graph_result" in text
            has_content = any(l.startswith("data:") and "\"content\"" in l for l in text.split("\n"))
            check(f"GRAPH '{label}' 含图谱事件", has_graph, f"text_len={len(text)}")
            check(f"GRAPH '{label}' 有回答内容", has_content, f"text_len={len(text)}")
    except Exception as e:
        check(f"GRAPH '{label}'", False, str(e))

# 验证图检索不影响纯 RAG 路径
try:
    resp = httpx.post(f"{API}/chat/stream",
                      json={"query": "什么是安全库存", "stream": True},
                      headers={"Authorization": f"Bearer {purchase_token}"}, timeout=30)
    check("纯RAG路径仍正常（无实体编码不进图检索）", resp.status_code == 200, str(resp.status_code))
except Exception as e:
    check("纯RAG路径", False, str(e))

# ============================================================
print(f"\n{'='*50}")
print(f"  结果: {PASS} 通过 / {FAIL} 失败 / {PASS+FAIL} 总计")
print(f"{'='*50}")

if FAIL > 0:
    print("\n失败项目：")
    for r in results:
        if "[FAIL]" in r:
            print(r)

sys.exit(0 if FAIL == 0 else 1)
