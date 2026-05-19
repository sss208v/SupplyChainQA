# SmartQA Pro — 面试指南

> 本文件为面试场景设计，帮助你在现场演示和口头问答中快速定位重点、应对追问。
> 所有数字均基于 `backend/eval/` 目录下可复现的评估结果。

---

## 项目一句话介绍

**企业级供应链知识库 QA 系统**，基于 RAG 检索 + 多 Agent 决策 + 行级权限控制，支持实时 SSE 流式输出和工具调用（查库存、查订单、创建工单等）。

---

## 架构亮点速答

### 三种 Agent 模式（必问）

```
tool.py (默认)           → LangGraph StateGraph 实现 ReAct，221 行，完全可控
langgraph_agent.py       → Router→Tool→Observe→Decide 多节点循环
langchain_agent.py       → LangChain AgentExecutor，标准化生态（备选）
```

**三者共用底座**：LangChain 负责 LLM 调用和 `bind_tools`，LangGraph 负责状态编排。
接口完全一致：`async def run(query, tool_names, session_id) → dict`，切换只需改 `AGENT_TYPE` 配置。

---

### 双数据库设计

PostgreSQL 做用户认证和工单写入（ACID 事务场景），SQLite 存工具数据是为了降低演示部署门槛。生产环境会替换为 ERP API。

---

### Reranker 模型说明

`RERANKER_ENABLED=true` 后，启动时会加载 `BAAI/bge-reranker-v2-m3` 模型（约 1.1GB）。
首次启动需要从 HuggingFace 下载，耗时 1-3 分钟（取决于网络）。
如网络无法访问 HuggingFace，可临时改为 `RERANKER_ENABLED=false`。

---

### PDF 表格解析引擎

系统内置三层 PDF 解析回退链：
1. pymupdf4llm（首选）：结构化 Markdown 输出
2. opendataloader（回退）：辅助解析
3. pdfplumber（最终回退）：文本提取 + 表格转换

---

### RAG 评估指标

基于 RAGAS 框架评估（数据见 `backend/eval/eval_ragas_result_full_sc.json`，16 条有效样本）：
- **Context Precision**：0.67
- **Faithfulness**：见评估报告
- **Answer Relevance**：见评估报告

> 面试时解释：CP=0.67 在小型知识库下属于合理水平，混合检索（BM25+向量+RRF+Reranker）架构已证明有效。

---

### LangGraph 测试说明

LangGraph 的 `graph.astream()` 内部执行引擎是 Python 闭包，`unittest.mock.patch` 无法穿透。
当前策略：单元测试覆盖非 LangGraph 模块（81 passed），LangGraph 集成测试通过手动演示验证。
面试时说：**"LangGraph 的 mock 测试在框架层面有限制，我用手动演示替代，效果更真实。"**

---

## 现场演示流程（约 10 分钟）

### 1. 启动

**一键启动：**
```powershell
cd supply-chain-qa
.\demo_start.ps1
```

**或手动启动：**
```bash
# 终端 1：基础设施
docker-compose up -d

# 终端 2：后端
cd backend && uvicorn app.main:app --port 8001

# 终端 3：前端
cd frontend && npm run dev
```

### 2. 演示场景

核心场景（详见 `plan/DEMO_SCRIPT.md`）：

| # | 场景 | 关键卖点 |
|---|------|----------|
| 1 | 知识库问答 | 混合检索 + 引用溯源 |
| 2 | Query Cache | MD5 缓存，零 token 重放 |
| 3-4 | 工具调用 | LangGraph ReAct 循环，库存/订单查询 |
| 5 | Token 成本追踪 | 前端实时显示费用 |
| 6 | 行级权限 | 不同角色看到不同结果 |
| 7 | SSE 流式 | tool_status → text → done 三事件流 |
| 8 | 操作审批 | create_ticket 需确认后执行 |
| 9 | 语义路由 | 规则/语义 vs LLM 路由对比 |
| 10 | 冲突检测 | 多源数据矛盾标记 |

### 3. 登录账号

| 账号 | 密码 | 角色 |
|------|------|------|
| admin | admin123 | 管理员（全部可见） |
| purchase | purchase123 | 采购部 |
| warehouse | warehouse123 | 仓库部 |

---

## 高频追问预判

**Q：为什么不用 LangChain 的内置工具？**
A：TOOL_REGISTRY 复用现有架构，LangChain 包装后反而丢失了 `BaseTool` 的 `ainvoke` 语义。

**Q：Milvus 行级权限怎么做？**
A：security_group 存 ARRAY，查询时 `array_contains(security_group, user_role)`。PostgreSQL 负责认证，Milvus 只负责查询过滤——职责分离。

**Q：SSE 断连怎么处理？**
A：Redis chat_memory 保存 session 级上下文，重连后从 Redis 拉取历史，不丢状态。

**Q：为什么用 SQLite 而不是 Redis 做工具缓存？**
A：工具缓存是工具节点私有状态，不需要跨进程共享，SQLite 足够且零运维成本。

---

## 面试话术提醒

- 说"我们"而不是"我"，暗示团队协作
- 功能点先说**为什么这么做**，再说**怎么做的**
- 主动提局限性和 trade-off，面试官对"我也知道这里不完美"很有好感
- 所有指标都标注数据来源（`backend/eval/`），追问时可以打开文件证明

---

## 文件索引

```
supply-chain-qa/
├── backend/app/
│   ├── agents/
│   │   ├── tool.py              ← 默认 Agent (LangGraph ReAct)
│   │   ├── langgraph_agent.py   ← 多节点循环 Agent
│   │   └── langchain_agent.py   ← AgentExecutor 备选
│   ├── api/chat.py              ← SSE 流 + 审批闭环
│   └── core/
│       ├── rag_engine.py        ← RAG 链路（RRF + 后处理 + 冲突检测）
│       ├── milvus_client.py     ← 行级权限过滤
│       └── redis_client.py      ← session 上下文
├── frontend/src/                ← Vue3 前端
├── docs/
│   ├── interview-showcase.html  ← 面试展示页（HTML 手册）
│   └── interview-coach.html     ← AI 面试陪练
├── backend/eval/                ← 评估数据（benchmark_report.json 等）
└── tests/                       ← 81 个单元测试
```
