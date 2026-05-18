# SmartQA 图谱增强 — Neo4j + 向量图融合检索 OpenSpec

> 版本: 1.0 | 日期: 2026-05-18 | 基线: v2.1 Agentic 升级已完成
> 原则: 只加一层图检索，不动现有多 Agent 架构；新增而非替换

---

## 1. 为什么升级

当前系统有两个根本局限，面试官大概率追问：

| 局限 | 表现 | 面试风险 |
|------|------|----------|
| 纯语义匹配 | "MAT-001 缺货"只召回文档片段，不做因果链推理 | "你这 RAG 不就是关键词匹配的升级版？" |
| 关系不可见 | 6 个工具各自查 SQLite，agent 靠 LLM 脑补关联 | "跨表 JOIN 的事为什么不直接查图？" |

**图检索解决的是"结构化推理"问题**——实体之间的 belong_to、contain、from、for 关系是确定的，不该依赖 LLM 脑补。Neo4j 一步 `MATCH` 就能遍历的链路，在 SQL 里需要多次 JOIN，在 RAG 里完全不可见。

这次升级的目标：**在现有语义检索旁加一条图检索路径，两路并行召回，统一排序后喂给 LLM。** 不替代 RAG，不替代 Agent，是补充。

---

## 2. 当前基线

| 维度 | 状态 |
|------|------|
| RAG 检索 | ✅ 自适应 RRF + 四层后处理（92 篇 2425 chunks） |
| 多 Agent | ✅ Orchestrator + 4 专域 Agent（Purchase/Inventory/Quality/Production） |
| 工具调用 | ✅ 6 工具（query_inventory/query_order/query_supplier/create_ticket/get_knowledge/get_datetime） |
| 意图路由 | ✅ 三级路由（规则/语义/LLM），含 GOAL 意图 |
| RBAC | ✅ 7 部门 role + security_group |
| 数据层 | ✅ SQLite Odoo schema（5 表，15 物料，3 订单，5 订单行，3 在途） |
| 测试 | ✅ pytest 66/66 passed，verify_demo 31/36（5 项待后端重启验证） |
| 面试 HTML | ✅ 31 导航项，含多 Agent 架构章节 |
| Git | ✅ 20+ commits on main |

---

## 3. 需求清单

### REQ-1: Neo4j 基础设施 [P0]

**Neo4j 版本**: `neo4j:5-community`（Community Edition，免费无限制）

**Docker 部署**（追加到 docker-compose.yml）:
```yaml
neo4j:
  image: neo4j:5-community
  container_name: smartqa-neo4j
  ports:
    - "7474:7474"   # HTTP
    - "7687:7687"   # Bolt
  environment:
    NEO4J_AUTH: neo4j/smartqa123
    NEO4J_PLUGINS: '["apoc"]'
  volumes:
    - neo4j_data:/data
    - neo4j_logs:/logs
  healthcheck:
    test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "smartqa123", "RETURN 1"]
```

**验收**:
- [ ] `docker compose up -d neo4j` 启动成功
- [ ] `curl http://localhost:7474` 返回 Neo4j 状态
- [ ] Bolt 7687 端口连通
- [ ] 后端 `/health` 新增 `neo4j: {connected: true}`

---

### REQ-2: 供应链图 Schema 与数据同步 [P0]

**节点类型（Labels）**:

| Label | 关键属性 | 来源表 | 预估节点数 |
|-------|---------|--------|-----------|
| `Supplier` | code, name, contact | purchase_order.partner_name | ~5 |
| `PurchaseOrder` | code, state, amount_total | purchase_order | ~3 |
| `OrderLine` | qty, price_unit, subtotal | purchase_order_line | ~5 |
| `Material` | code, name, qty_available, standard_price | product_product | 15 |
| `StockMove` | qty, state, expected_date | stock_move | ~3 |
| `Ticket` | code, priority, stage | maintenance_ticket | 动态 |

**关系类型**:

| 关系 | 方向 | 含义 |
|------|------|------|
| `[:FROM]` | PurchaseOrder → Supplier | 订单归属供应商 |
| `[:CONTAINS]` | PurchaseOrder → OrderLine | 订单包含明细行 |
| `[:FOR]` | OrderLine → Material | 明细行对应物料 |
| `[:HAS_MOVE]` | Material → StockMove | 物料有在途库存 |
| `[:RELATED_TO]` | Ticket → Material | 工单关联物料 |

**数据同步策略**: 启动时全量同步（< 50 节点，< 1 秒），之后每次写操作增量同步。实现为 `main.py` 的 lifespan hook：

```python
async def _sync_graph():
    """SQLite → Neo4j 全量同步（启动时执行一次）"""
    graph.clear()  # 清空旧数据
    # 1. 物料节点
    for row in sqlite("SELECT * FROM product_product"):
        graph.merge_node("Material", {"code": row["default_code"], ...})
    # 2-6. 依次同步其余表 + 关系
```

**验收**:
- [ ] 启动后 Neo4j 含全部 6 种 Label 节点
- [ ] 关系类型全部 5 种存在
- [ ] `MATCH (n) RETURN count(n)` > 25
- [ ] 重启不丢失、不重复（MERGE 语义）

---

### REQ-3: 图查询引擎 — 3 个供应链场景 [P0]

**不追求通用 NLP→Cypher**，只为 3 个场景写模板化 Cypher，参数由实体提取器填空。

**实体提取器**（轻量规则，不用 LLM）:
```python
def extract_entities(query: str) -> dict:
    # 正则提取物料编码 MAT-\d+、订单号 PO-\d+、供应商名、工单号 TK-\d+
    return {"material_codes": [...], "order_codes": [...], ...}
```

**场景 1: 库存短缺评估** — 从物料出发，找在途订单 + 供应商

```cypher
MATCH (m:Material {code: $material_code})
OPTIONAL MATCH (m)-[:HAS_MOVE]->(sm:StockMove {state: 'assigned'})
OPTIONAL MATCH (m)<-[:FOR]-(ol:OrderLine)<-[:CONTAINS]-(po:PurchaseOrder)-[:FROM]->(s:Supplier)
RETURN m.code AS material, m.name, m.qty_available AS stock,
       collect(DISTINCT {code: sm.origin, qty: sm.qty, expected: sm.expected_date}) AS in_transit,
       collect(DISTINCT {supplier: s.name, order: po.code, qty: ol.qty, planned: ol.date_planned}) AS on_order
```

**场景 2: 质量追溯** — 从物料找关联工单 + 上游供应商

```cypher
MATCH (m:Material {code: $material_code})
OPTIONAL MATCH (m)-[:RELATED_TO]->(t:Ticket)
OPTIONAL MATCH (m)<-[:FOR]-(ol:OrderLine)<-[:CONTAINS]-(po:PurchaseOrder)-[:FROM]->(s:Supplier)
RETURN m.code AS material, m.name,
       collect(DISTINCT {code: t.code, priority: t.priority}) AS tickets,
       collect(DISTINCT {supplier: s.name, order: po.code, batch: ol.qty}) AS suppliers
```

**场景 3: 供应商影响分析** — 从供应商出发，找所有物料 + 受影响工单

```cypher
MATCH (s:Supplier {name: $supplier_name})<-[:FROM]-(po:PurchaseOrder)-[:CONTAINS]->(ol:OrderLine)-[:FOR]->(m:Material)
OPTIONAL MATCH (m)-[:RELATED_TO]->(t:Ticket)
RETURN s.name AS supplier,
       collect(DISTINCT {material: m.code, name: m.name, stock: m.qty_available}) AS materials,
       collect(DISTINCT {ticket: t.code, priority: t.priority}) AS affected_tickets
```

**验收**:
- [ ] 3 个 Cypher 模板都能在 Neo4j Browser 中手动执行并返回结果
- [ ] 实体提取器对 "查 MAT-001 有没有质量问题" 正确提取 `material_codes: ["MAT-001"]`
- [ ] 模板空参数时返回空结果而非崩溃

---

### REQ-4: 三路融合排序 [P0]

**当前 RRF 公式**（v2.1，双路）:
```
RRF_score(d) = Σ 1/(k + rank_i(d))
```
融合向量检索 + BM25 两路排名。

**扩展为三路（不改现有公式，新增融合权重）**:

```
最终得分 = α · RRF_score(向量+BM25) + β · graph_score

其中 graph_score = 1.0（图遍历命中）或 0.0（未命中）
α = 0.7, β = 0.3（可配置）
```

**设计理由**: 图检索结果不是"排名"而是"精确命中"——实体匹配到了就是匹配到了。用二值权重而非 RRF 排名，语义上更准确。

**实现**: 在 `rag_engine.py` 的 `_rrf_fusion()` 方法后追加图融合步骤：
```python
def _fuse_with_graph(self, rrf_results, graph_results, alpha=0.7, beta=0.3):
    for item in rrf_results:
        entity = item.get("entity")
        if entity and entity in graph_results.get("matched_entities", []):
            item["score"] = alpha * item["rrf_score"] + beta * 1.0
    return rrf_results
```

**验收**:
- [ ] 图命中的结果排序提升（排在前 3 位）
- [ ] 图未命中时排序不变（退化到原 RRF）
- [ ] alpha/beta 可通过 config.py 调整

---

### REQ-5: 意图路由新增 GRAPH 类型 [P0]

在 `router.py` 新增意图：

```python
class IntentType(str, Enum):
    GREETING = "greeting"
    RAG_ANSWER = "rag_answer"
    TOOL_CALL = "tool_call"      # 单工具
    GOAL = "goal"                # 多步编排
    HYBRID = "hybrid"            # RAG + 工具
    GRAPH_QUERY = "graph_query"  # 图检索（新增）
    UNCLEAR = "unclear"
```

**路由规则**（在 `_rule_match` 的位置：TOOL_CALL 之后、GOAL 之前）:
```python
# 2.5. 图检索关键词（含实体编码的结构化查询）
_graph_keywords = [
    "哪些物料", "什么供应商", "影响的物料", "关联工单",
    "在途订单", "上游供应商", "影响的订单", "缺货影响",
]
# 含 MAT-/PO-/TK-/SUP- 编码且不含"查""查询"等工具关键词
```

**chat.py 新增 GRAPH 分支**:
```python
elif intent == IntentType.GRAPH_QUERY:
    yield _sse_format({"type": "graph_query_start", "entities": extracted})
    result = await graph_engine.query(safe_query, entities)
    yield _sse_format({"type": "graph_result", "pattern": result["pattern"], "rows": result["rows"]})
    # 图结果注入 LLM 上下文
    graph_context = _format_graph_context(result)
    answer = await llm.ainvoke([..., HumanMessage(content=f"图谱查询结果:\n{graph_context}\n\n用户问题: {safe_query}")])
    yield _sse_format({"type": "content", "content": answer})
```

**验收**:
- [ ] "MAT-001 缺货会影响哪些物料" → 路由为 `graph_query`
- [ ] "什么是安全库存" → 仍走 `rag_answer`（不影响现有路由）
- [ ] SSE 事件含 `graph_query_start` + `graph_result`
- [ ] pytest 新增 3 个路由测试

---

### REQ-6: 面试展示更新 [P1]

**架构图**: 在 `docs/architecture-agentic.svg` 基础上新增图检索分支（Neo4j 节点 + Bolt 协议箭头）

**HTML 新增**: `docs/interview-showcase.html` 新增 1 章节——
- **图谱增强检索**（Neo4j Cypher 模板 + 三路融合公式 + 面试话术）

**面试话术新增**:
- "为什么用 Neo4j 不用 NetworkX？"——图数据库是工业标准，Cypher 是你的简历技能点
- "图和向量怎么融合？"——图做精确实体关系匹配（确定性），向量做语义近似匹配（概率性），加权融合
- "图检索延迟多少？"——Cypher 查询 < 10ms，瓶颈仍在 LLM

**验收**:
- [ ] 架构图含 Neo4j 节点，浅色主题，无重叠
- [ ] HTML 侧边栏 31 → 32+ 项
- [ ] 面试话术可由 LLM 直接引用

---

## 4. 明确砍掉

| 砍掉 | 理由 |
|------|------|
| NL→Cypher 自动翻译（LLM 生成查询） | 3 个场景模板化足够，LLM 生成 Cypher 不可控，面试时反而暴露幻觉风险 |
| 图写入接口（前端可视化编辑） | SQLite 工具已覆盖写操作，图只是读加速层 |
| Neo4j APOC 过程库 | 不需要图算法（PageRank/社区检测），只用基础 Cypher |
| GDS（Graph Data Science）插件 | 供应链图太小（< 50 节点），不需要 ML 图嵌入 |
| GraphRAG 完整方案（Microsoft/neo4j-genai） | 太重。项目的图查询不需要 LLM 生成 Cypher |
| 图 RBAC 权限 | 节点级权限对 < 50 节点的图无意义，SQLite RBAC 已覆盖 |

---

## 5. 实施任务

### Phase 1: Neo4j 基础设施（预计 1.5 小时）

| ID | 任务 | 文件 |
|----|------|------|
| T1 | docker-compose.yml 追加 Neo4j 服务 | `docker-compose.yml`（修改） |
| T2 | neo4j_client.py — 连接管理 + 健康检查 | `backend/app/core/neo4j_client.py`（新增） |
| T3 | config.py 追加 Neo4j 配置 | `backend/app/config.py`（修改） |
| T4 | main.py lifespan 追加图数据同步 | `backend/app/main.py`（修改） |
| T5 | Neo4j 连接验证 — /health 端点新增字段 | `backend/app/main.py`（修改） |

### Phase 2: 图查询引擎（预计 2 小时）

| ID | 任务 | 文件 |
|----|------|------|
| T6 | graph_engine.py — 实体提取器 + 3 个 Cypher 模板 | `backend/app/core/graph_engine.py`（新增） |
| T7 | rag_engine.py — 三路融合排序（_fuse_with_graph） | `backend/app/core/rag_engine.py`（修改） |
| T8 | router.py — 新增 GRAPH_QUERY 意图 + 关键词 | `backend/app/agents/router.py`（修改） |
| T9 | chat.py — GRAPH 分支 SSE 事件 + LLM 生成 | `backend/app/api/chat.py`（修改） |
| T10 | 单元测试 — test_graph_engine.py（实体提取 + Cypher 模板） | `backend/tests/test_graph_engine.py`（新增） |
| T11 | 路由测试 — test_router.py 新增 GRAPH 意图 | `backend/tests/test_router.py`（修改） |

### Phase 3: 验证与展示（预计 1 小时）

| ID | 任务 | 文件 |
|----|------|------|
| T12 | verify_demo.py 新增 3 个图检索测试 | `scripts/verify_demo.py`（修改） |
| T13 | 架构图更新 — 新增 Neo4j 分支 | `docs/architecture-agentic.svg`（修改） |
| T14 | 面试 HTML 新增图谱章节 | `docs/interview-showcase.html`（修改） |
| T15 | 全量回归 — pytest + verify_demo | 终端 |

---

## 6. 验收检查表

```
Phase 1 完成:
[ ] docker compose up -d neo4j 成功，7474/7687 端口监听
[ ] backend /health 返回 neo4j: {connected: true}
[ ] Neo4j Browser (http://localhost:7474) 可见 6 种 Label 节点
[ ] MATCH (n) RETURN count(n) >= 25

Phase 2 完成:
[ ] 场景 1: "MAT-001 缺货" → 返回在途订单 + 供应商
[ ] 场景 2: "MAT-002 质量追溯" → 返回工单 + 上游供应商
[ ] 场景 3: "供应商 PO-001 延迟" → 返回受影响物料 + 工单
[ ] graph_query 意图正确触发（entity 提取 → Cypher 匹配 → 三路融合）
[ ] 图未命中时退化为纯 RAG（不报错）
[ ] pytest 新增测试全通过
[ ] 66 项旧测试零回归

Phase 3 完成:
[ ] verify_demo.py 新增图检索场景通过
[ ] 架构图含 Neo4j，渲染正确
[ ] HTML 侧边栏 32+ 项
[ ] 面试话术覆盖 Neo4j 设计决策
```

---

## 7. 面试话术模板

**为什么加 Neo4j：**
「供应链数据本质上是图。物料→订单→供应商是一个天然的三跳关系。SQLite 的 JOIN 能做，但语义上不直观——面试官看一眼 Cypher 的 `MATCH (m:Material)-[:HAS_MOVE]->(sm:StockMove)` 就知道系统在做什么。而且图检索和向量检索解决的是两种不同类型的问题：图做精确关系匹配，向量做语义近似匹配。两者互补。」

**Neo4j vs NetworkX 的选择：**
「NetworkX 够用，但 Neo4j 更规范。一是 Cypher 是图查询的事实标准，简历上有这个技能点就是加分项。二是面试场景下，打开 Neo4j Browser 能看到节点-关系可视化，比一行 Python 输出有说服力得多。三是 Community Edition 免费，Docker 拉起来只要 5 秒。」

**三路融合的设计哲学：**
「向量检索解决的是『这段话在说什么』，图检索解决的是『这些实体之间什么关系』。两者的分数体系不同——向量排名是连续的，图匹配是二值的。所以我用了加权融合而非把图结果塞进 RRF。这个设计决策在面试时可以说：我选择了语义正确的方案而非代码简单的方案。」

**延迟不是问题：**
「Cypher 查询 < 10ms，Neo4j 是内存图。真正的延迟在 LLM 生成，和加不加图没关系。图的优势是用更精准的上下文减少 LLM 的补全压力，实际端到端延迟反而可能降低。」

---

## 8. 文件变更总览

| 文件 | 操作 | 行数估 |
|------|------|--------|
| `docker-compose.yml` | 改 | +18 |
| `backend/app/config.py` | 改 | +8 |
| `backend/app/core/neo4j_client.py` | 新增 | ~100 |
| `backend/app/core/graph_engine.py` | 新增 | ~200 |
| `backend/app/core/rag_engine.py` | 改 | ~50 |
| `backend/app/agents/router.py` | 改 | ~25 |
| `backend/app/api/chat.py` | 改 | ~40 |
| `backend/app/main.py` | 改 | ~20 |
| `backend/tests/test_graph_engine.py` | 新增 | ~100 |
| `backend/tests/test_router.py` | 改 | +20 |
| `scripts/verify_demo.py` | 改 | +30 |
| `docs/architecture-agentic.svg` | 改 | 纯视觉 |
| `docs/interview-showcase.html` | 改 | +40 |

---

> **下一步**: 确认后开始实施 Phase 1 — Neo4j Docker 部署 + 连接层。
