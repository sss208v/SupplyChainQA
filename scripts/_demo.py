import httpx, json, sys

API = "http://localhost:8001/api/v1"

def login(user, pw):
    try:
        r = httpx.post(f"{API}/auth/login", json={"username":user,"password":pw}, timeout=5)
        data = r.json()
        return data.get("token", "")
    except Exception as e:
        print(f"  login failed: {e}")
        return ""

def sse_show(query, token, label=""):
    try:
        r = httpx.post(f"{API}/chat/stream", json={"query":query,"stream":True},
                       headers={"Authorization":f"Bearer {token}"}, timeout=90)
        intent = "?"
        events = []
        answer = ""
        for line in r.text.split('\n'):
            if line.startswith('data:'):
                d = line[5:].strip()
                try:
                    j = json.loads(d)
                    t = j.get('type','')
                    if t == 'route': intent = j.get('intent','?')
                    elif t in ('tool_status','tool_call','orchestrator_plan','agent_step',
                               'graph_query_start','graph_result'):
                        events.append(t)
                    elif t == 'content':
                        answer = j.get('content','')
                    elif t == 'error':
                        events.append(f"ERR:{j.get('message','')[:50]}")
                except: pass
        print(f"  intent={intent}  events={events}")
        if answer:
            print(f"  answer: {answer[:250]}")
        print()
    except Exception as e:
        print(f"  ERROR: {e}\n")

print("=" * 60)
print("  SmartQA Pro - Interview Demo")
print("=" * 60)
print()

# Login
token_p = login("purchase", "123456")
token_f = login("finance", "123456")
if not token_p:
    print("Login failed - is backend ready?")
    sys.exit(1)

# 1. Health
print("1. Health Check")
r = httpx.get("http://localhost:8001/health", timeout=5)
h = r.json()
print(f"  Milvus:{h['services']['milvus']['connected']} Redis:{h['services']['redis']['connected']} PG:{h['services']['postgres']['connected']} Neo4j:{h['services']['neo4j']['connected']}  docs:{h['knowledge_docs_count']}")
print()

# 2. RAG
print("2. RAG - 'what is safety stock'")
sse_show("什么是安全库存", token_p)

# 3. Tool
print("3. Tool Call - query inventory")
sse_show("查物料 MAT-001 的库存", token_p)

# 4. Graph
print("4. Neo4j Graph - impact analysis")
sse_show("MAT-001 缺货会影响哪些物料", token_p)

# 5. GOAL
print("5. GOAL Orchestration - risk assessment")
sse_show("帮我评估 MAT-001 库存风险", token_p)

# 6. Permission
print("6. RBAC - finance tries query_inventory")
r = httpx.post(f"{API}/tools/call", json={"query":"查物料 MAT-001 的库存"},
               headers={"Authorization":f"Bearer {token_f}"}, timeout=10)
try:
    print(f"  finance: {r.json().get('answer','')[:150]}")
except:
    print(f"  status={r.status_code}")

print()
print("=" * 60)
print("  Demo Complete")
print("=" * 60)
