# SmartQA Agentic 升级 — OpenSpec 需求规格

> 版本: 2.0 | 日期: 2026-05-17 | 定位: 供应链智能问答系统
> 原则: 只做供应链 QA 必须的，砍掉所有不直接增强"供应链问答能力"的东西

---

## 1. 为什么升级

当前 SmartQA 是一个优秀的 RAG + 单 Agent 系统（28/28 全链路通过）。但有一个根本局限：

**供应链问题天然跨部门，单 Agent 天然不跨。**

用户问"MAT-001 库存不够怎么办"——这是个跨 Inventory + Purchase + Production 的问题。单 Agent 只能返回一个工具的查询结果，做不到"库存 → 在途 → 缺口计算 → 采购建议"的多步推理链。

这次升级的核心就一个目标：**让系统能端到端处理 3 个供应链跨域场景。** 不做通用 Agentic ERP，做一个"供应链领域的 Agentic QA"。

---

## 2. 当前基线

| 维度 | 状态 |
|------|------|
| RAG 检索 | ✅ 自适应 RRF + 四层后处理（92 篇 2425 chunks） |
| 单 Agent | ✅ LangGraph ToolNode，6 工具，5 轮收敛 |
| RBAC | ✅ 7 部门 role + security_group 数组列 |
| 全链路 | ✅ verify_demo.py 28/28 通过 |
| 冲突检测 | ✅ 检索 + 前端 SSE 展示 |
| 多模态 | ✅ CLIP 图文检索 |
| 面试 HTML | ✅ 28 导航项，话术一致性已审计 |

---

## 3. 需求清单

### REQ-1: 4 个供应链专域 Agent [P0]

砍掉 Finance/Logistics — 供应链 QA 用不到，保留 4 个核心：

| Agent | 工具 | 典型问题 |
|-------|------|---------|
| PurchaseAgent | query_order, query_supplier | 采购单状态、供应商交期、在途总量 |
| InventoryAgent | query_inventory | 库存水位、安全库存、物料追溯 |
| QualityAgent | get_knowledge, create_ticket | 质量标准查询、异常工单创建 |
| ProductionAgent | create_ticket, query_inventory | 缺料影响评估、工单创建 |

**实现**: 在现有 `agents/tool.py` 的 LangGraph Agent 基础上，不改框架，只做 **tool binding 拆分**——同一个 LLM 实例 + 同一个 StateGraph 模式，只是每个 Agent 的 `bind_tools()` 传入不同的工具子集。

**验收**:
- [ ] 4 个 Agent 类实现，`bind_tools` 传不同工具列表
- [ ] 路由规则：按工具名自动匹配对应 Agent（`query_order` → PurchaseAgent）
- [ ] 现有 28 项回归测试全通过
- [ ] 新增 4 个单 Agent 单元测试

---

### REQ-2: 供应链跨域工作流（Plan-Execute）[P0]

不做通用 workflow engine，只做 **3 个供应链场景的上下文链式调用**。

**核心机制**: Orchestrator 接收跨域查询 → LLM 拆解为 Agent 调用序列 → 按序执行 → 每步结果作为下一步的上下文。

```
输入: "MAT-001 库存不够了，帮我评估"

Orchestrator 分析（一次 LLM 调用）:
  意图: 库存短缺评估
  Plan:
    1. InventoryAgent: 查 MAT-001 库存 + 安全库存
    2. PurchaseAgent: 查 MAT-001 在途采购单
    3. 内置: 计算缺口 = 安全库存 - (当前 + 在途)
    4. 若缺口 > 0: ProductionAgent 建议创建工单

执行引擎:
  1. call InventoryAgent → {库存: 50, 安全库存: 200}
  2. call PurchaseAgent  → {PO-001: 100件, ETA 5天}
  3. 计算: 50+100=150 < 200, 缺口 50
  4. 生成建议 + 追问是否创建工单
```

**只保证 3 个跨域场景**:

| 场景 | Agent 链 | 面试价值 |
|------|---------|---------|
| 库存短缺评估 | Inventory → Purchase → Production | 最经典供应链场景 |
| 质量异常追溯 | Quality → Inventory → Purchase | 合规 + 追溯能力 |
| 供应商延迟影响 | Purchase → Inventory → Production | 风险传导分析 |

**验收**:
- [ ] Orchestrator 能对 3 个场景生成正确的 Agent 调用序列
- [ ] 序列按依赖关系顺序执行（不支持并行，面试项目没必要）
- [ ] 每步结果注入下一步上下文
- [ ] 单次跨域查询延迟 < 8 秒（LLM 推理 + 3-4 次工具调用）
- [ ] SSE 事件新增 `orchestrator_plan` / `agent_step` 类型
- [ ] 新增 3 个跨域场景端到端测试

---

### REQ-3: 意图分流 — goal 还是 query [P1]

在现有 Router 前加一层简单分类，只分两种：

```
用户输入 → IntentClassifier (1 次 LLM 调用，复用现有)
├── goal (目标型) → Orchestrator → 跨域工作流
│   "MAT-001 库存不够怎么办"
│   "帮我评估供应商延迟的影响"
│   "这个质量问题需要追溯哪些物料"
│
└── query (查询型) → 现有路径（RAG 或单工具）
    "什么是安全库存"         → RAG
    "查 MAT-001 库存"        → 单工具 InventoryAgent
    "PO-001 什么状态"        → 单工具 PurchaseAgent
```

不单独加 LLM 调用——复用现有 Router 的分类结果，加一个 `needs_orchestration` 布尔标记。

**验收**:
- [ ] goal 查询走 Orchestrator 路径
- [ ] query 查询走现有路径（行为不变）
- [ ] 额外延迟 < 0（复用现有 LLM 调用）
- [ ] 现有 28 项测试全通过（query 路径不变）

---

### REQ-4: 面试展示 [P0]

更新 `docs/interview-showcase.html`，新增 3 个章节 + 更新架构图：

**新增内容**:
1. **Agentic 供应链 QA 架构** — 新架构图（Orchestrator + 4 Agent）
2. **跨域场景演示** — 3 个场景的 step-by-step 执行轨迹 + 截图
3. **与行业对标** — SmartQA vs SAP Joule vs Oracle Agentic Apps 对比表

**更新内容**:
- 架构图从"单 Agent 6 工具"更新为"Orchestrator + 4 Agent"
- 工具列表标注所属 Agent
- 面试话术新增"为什么从单 Agent 升级到多 Agent"

**验收**:
- [ ] `docs/architecture-agentic.svg` 新架构图（浅色主题，无重叠）
- [ ] HTML 侧边栏从 28 → 32+ 项
- [ ] 3 个跨域场景有完整执行轨迹可展示
- [ ] 面试话术覆盖：架构升级动机 / 3 场景 / SAP Oracle 对标

---

## 4. 明确砍掉

| 砍掉 | 理由 |
|------|------|
| FinanceAgent / LogisticsAgent | 供应链 QA 用不到，面试也不问 |
| 多 Agent 并行执行 | 3 个场景的 Agent 链都是串行的，并行无实际收益 |
| Replan 循环 | Plan-Execute 够用，Replan 只在 Plan 失败时触发（retry 已覆盖） |
| AgentMemory (Redis 持久化) | 单次查询内上下文已存在 state 里，跨轮次恢复对 QA 系统意义不大 |
| Clean Core 架构重构 | chat.py 拆分可以做（提取 orchestrator），但不作为独立需求 |
| 异常处理独立模块 | 现有 retry + approval + clarify 已覆盖 80% |
| 新 Agent 框架 | 不引入 CrewAI/AutoGen，LangGraph 够用 |

---

## 5. 实施任务

### Phase 1: Agent 拆分（预计 3-4 小时）

| ID | 任务 | 文件 |
|----|------|------|
| T1 | 创建 Agent 基类 | `agents/domain_agent.py` (新增) |
| T2 | 实现 4 个专域 Agent | `agents/purchase_agent.py` 等 4 个文件 |
| T3 | 单 Agent 路由 — 按工具名分发 | `agents/router.py` (修改) |
| T4 | 4 个 Agent 的单元测试 | `tests/test_domain_agents.py` (新增) |
| T5 | 回归验证 — 28 项全量 | `scripts/verify_demo.py` |

### Phase 2: 跨域工作流（预计 4-6 小时）

| ID | 任务 | 文件 |
|----|------|------|
| T6 | Orchestrator plan 生成 | `agents/orchestrator.py` (新增) |
| T7 | 顺序执行引擎 | `agents/orchestrator.py` |
| T8 | IntentClassifier — goal/query 分流 | `agents/router.py` (修改) |
| T9 | chat.py 集成 — SSE 事件 + 路径分发 | `api/chat.py` (修改) |
| T10 | 3 个场景端到端测试 | `tests/test_cross_domain.py` (新增) |
| T11 | 回归验证 | `scripts/verify_demo.py` |

### Phase 3: 展示（预计 2-3 小时）

| ID | 任务 | 文件 |
|----|------|------|
| T12 | 新架构图 | `docs/architecture-agentic.svg` (新增) |
| T13 | HTML 扩展 — 3 新章节 + 话术 | `docs/interview-showcase.html` (修改) |
| T14 | 全链路最终验证 | `scripts/verify_demo.py` + 手动场景测试 |

---

## 6. 验收检查表

```
Phase 1 完成:
[ ] 4 个 Agent 全部 import 成功
[ ] 单工具查询走对应 Agent（"查库存" → InventoryAgent）
[ ] 28 项回归测试全通过
[ ] 4 个单 Agent 单元测试全通过

Phase 2 完成:
[ ] 场景 1: "MAT-001 库存不够" → Inventory → Purchase → 缺口计算 → 建议
[ ] 场景 2: "MAT-002 质量问题追溯" → Quality → Inventory → Purchase
[ ] 场景 3: "供应商 PO-001 延迟影响" → Purchase → Inventory → Production
[ ] goal 查询走 Orchestrator，query 查询走原路径
[ ] 3 个场景端到端测试全通过
[ ] 28 项回归测试全通过

Phase 3 完成:
[ ] 架构图正确渲染，无重叠
[ ] HTML 侧边栏 32+ 项
[ ] 3 个场景有可演示的执行轨迹
[ ] 面试话术就绪
```

---

## 7. 面试话术模板

**为什么升级到多 Agent:**
「供应链问题的本质是跨部门的。用户问库存不够怎么办，系统需要同时查库存、在途订单、安全库存阈值，然后算缺口、给建议。单 Agent 只能一个一个工具调，多 Agent 让 Orchestrator 把问题拆成 3-4 步自动联动——这更接近真实供应链系统的运作方式。SAP 今年在汉诺威发布的制造+物流+资产三 Agent 协同，和这个思路是一样的。」

**和 SAP/Oracle 的异同:**
「SAP 和 Oracle 做 Agentic ERP 的优势是有 140 万企业客户和几十年真实数据，Agent 嵌入在 S/4HANA 或 Oracle Fusion 的生产环境里。SmartQA 是一个原型系统，但在架构设计上和他们是同源的——都是专域 Agent + 编排层 + 工具绑定。区别是我们对接 SQLite 模拟库，他们对接真实 ERP 模块，但 Agent 层的设计模式通用。」

**3 个场景的话术要点:**
- 库存短缺 — 强调"不是写死的 if-else，是 Agent 根据实时数据动态决策"
- 质量追溯 — 强调"跨部门串联能力，从质量问题出发自动找到受影响的物料和订单"
- 供应商延迟 — 强调"风险传导分析，不只是告诉你延迟了，还会评估对生产的影响"

---

> **下一步**: 确认后开始实施 Phase 1。
