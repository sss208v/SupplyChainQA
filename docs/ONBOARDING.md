# Supply Chain QA - 供应链智能问答系统 上手指南

> 基于 `understand-onboard` skill 自动生成
> 数据源: `.understand-anything/knowledge-graph.json`
> 最后更新: 2026-07-03（RRF 全参数调优后更新）

---

## 0. 一句话总结

**面向制造业供应链场景的 RAG + Multi-Agent 智能问答系统**。用户问"上季度工单里 MAT-001 缺货影响哪些订单?",系统自动判断:这条是"业务查询",走 SQL 路径;还是"知识查询",走 RAG 路径;还是混合查询。

---

## 1. Project Overview

| 维度 | 内容 |
|---|---|
| **项目名** | Supply Chain QA |
| **场景** | 制造业供应链 |
| **架构特点** | 双链路驱动(知识库检索 + 业务系统查询) |
| **核心能力** | RAG / Graph RAG / NL2SQL / 多 Agent 协作 / 行级权限 / SSE 流式 |
| **语言** | Python + JavaScript + Vue |
| **框架** | FastAPI / LangChain / LangGraph / Vue3 / Element Plus |
| **存储** | Milvus(向量) + Neo4j(图) + Redis(缓存) + PostgreSQL(业务) |
| **可观测** | Langfuse 全链路 trace |

---

## 2. 架构分层(5 层)

```
┌─────────────────────────────────────────────────────┐
│  frontend   Vue3 + Element Plus + Pinia(5 页面)     │
├─────────────────────────────────────────────────────┤
│  api        FastAPI REST + SSE 端点(6 个路由)       │
├─────────────────────────────────────────────────────┤
│  agent      LangGraph/LangChain + 7 个业务 Agent    │
├─────────────────────────────────────────────────────┤
│  core       RAG / Graph / 工具 / 查询分析 / 评估    │
├─────────────────────────────────────────────────────┤
│  data       PostgreSQL / Milvus / Redis / Neo4j     │
└─────────────────────────────────────────────────────┘
```

每层职责:
- **frontend**: 聊天界面 + 知识库管理 + 评估可视化(雷达图)
- **api**: REST 端点 + SSE 流式对话(9 种事件类型)
- **agent**: 三级意图路由 + ReAct 主 Agent + 7 个领域 Agent
- **core**: 真正的"活" — RAG 引擎、Graph 引擎、工具调用、置信度评估
- **data**: 7 个核心 Docker 服务（PostgreSQL / Milvus / Neo4j / Redis / etcd / minio）。开发版 docker-compose 另有 backend/frontend 容器（需构建）、attu（Milvus UI）和 redisinsight（Redis UI）可选。

---

## 3. Key Concepts(项目独有的 6 个)

| 概念 | 解释 | 在哪 |
|---|---|---|
| **Loop Breaker** | Agent ReAct 循环的熔断器,25 轮自动收敛,防死循环 | `agents/tool.py` |
| **Semantic Self-Correction** | 工具调用失败时,语义相似度去重+自动修正 | `agents/tool.py` + `core/neo4j_client.py` |
| **Query-Type-Aware RRF** | 查询类型感知的自适应 RRF 权重 — precise 查询 BM25×1.25、semantic 查询向量×2.25、default 场景 BM25×1.75+向量×1.25。全部通过 optuna TPE 贝叶斯优化在 57Q 上确定（见 eval/rrf_full_tuning_report.md） | `core/rag/engine.py` + `config.py` |
| **Three-Level Routing** | 规则<1ms → 语义<10ms → LLM~2.5s,三级级联 | `agents/router.py` |
| **Graph RAG (Hybrid)** | Neo4j 实体关系图 + 关键词重叠阈值 0.2 才注入 | `core/rag/engine.py` Graph 注入段 |
| **Row-Level Security** | RBAC + 字段级脱敏 + Milvus 表达式过滤 | `core/auth.py:90/190` + `core/milvus_client.py:326` |

---

## 4. Guided Tour(3 步走完整个系统)

### Step 1: 请求处理全链路(从用户输入到响应)

```
Chat/index.vue (用户输入)
  ↓ POST /api/v1/chat (SSE 流式)
api/chat.py (1139 行的流式主循环)
  ↓ 根据 query 分发
agents/router.py (三级路由:规则/语义/LLM)
  ↓
agents/tool.py (ReAct 主 Agent) ─→ agents/rag.py ─→ core/rag_engine.py
                                   └─→ core/graph_engine.py (Graph RAG)
  ↓
SSE 流式返回(9 种事件类型)
```

### Step 2: SuperPowers 自愈机制(系统韧性)

- **Loop Breaker**: ReAct 循环超过 25 轮强制熔断,返回"未能完成"而不是死循环
- **Semantic Self-Correction**: 工具返回错误时,不是直接返回 error,而是相似度匹配历史成功案例,自动重试

### Step 3: RAG 检索管道(四阶段)

```
query_analyzer (查询分析)
  ↓
rag_engine (混合检索: Milvus + BM25)
  ↓ Query-Type-Aware RRF
graph_engine (图谱增强: Neo4j 2-hop 子图)
  ↓ Critic 关键词重叠 > 0.2 才注入
四层后处理 (rerank / dedup / filter / format)
  ↓
BGE-Reranker 精排
  ↓
最终候选 chunks
```

---

## 5. File Map(按层组织)

### 5.1 API 层(13 文件)
- `backend/app/main.py` — FastAPI 应用入口(中间件/路由/默认账户)
- `backend/app/api/chat.py` — 对话 API(1139 行 SSE 流式主循环,9 种事件)
- `backend/app/api/auth.py` — 认证 API(RBAC + JWT)
- `backend/app/api/knowledge.py` — 知识库 CRUD
- `backend/app/api/tool.py` — 工具 API
- `backend/app/api/evaluate.py` — 评估 API(ECharts 雷达图)
- `backend/app/api/feedback.py` — 反馈 API

### 5.2 Agent 层(12 文件)
- `backend/app/agents/router.py` — **三级意图路由器**(核心)
- `backend/app/agents/tool.py` — **ToolAgent (LangGraph ReAct)**(核心)
- `backend/app/agents/langgraph_agent.py` — LangGraph 编排(4 阶段:Router→Tool→Observe→Decide)
- `backend/app/agents/langchain_agent.py` — LangChain AgentExecutor(备选)
- `backend/app/agents/agent_router.py` — Agent 路由器(按场景自动选 Agent 类型)
- `backend/app/agents/orchestrator.py` — 多 Agent 编排
- `backend/app/agents/rag.py` — RAG Agent
- `backend/app/agents/domain_agent.py` — Domain Agent 基类
- `backend/app/agents/{inventory,purchase,quality,production}_agent.py` — 4 个业务 Agent

### 5.3 Core 层(23 文件,重点)
- `backend/app/core/rag/engine.py` — **RAG 引擎主入口**(753 行,你刚合并的 180-270 就在这)
- `backend/app/core/rag/graph_engine.py` — Graph RAG 引擎
- `backend/app/core/semantic_router.py` — 语义路由(零 token 消耗，per-intent 阈值 + margin 判据)
- `backend/app/core/intent_routes.py` — 路由配置加载器(data/intent_routes.json 热加载 + 工具名校验)
- `backend/app/core/llm_router.py` — 多模型 LLM 路由
- `backend/app/core/tool_engine.py` — 工具引擎
- `backend/app/core/observability.py` — Langfuse 可观测性
- `backend/app/core/retry.py` — 指数退避 + 熔断(184 行)
- `backend/app/core/text_to_sql.py` — **Text-to-SQL(你刚升级到 492 行)**
- `backend/app/core/keyword_coverage.py` — 关键词覆盖护栏

### 5.4 Data 层(7 个核心 Docker 服务)
- `supply-chain-qa-postgres`(业务数据)
- `supply-chain-qa-milvus`(向量)
- `supply-chain-qa-neo4j`(知识图谱)
- `supply-chain-qa-redis`(缓存)
- `supply-chain-qa-etcd` / `supply-chain-qa-minio`(Milvus 依赖)

> 开发版 `docker-compose.yml` 含 11 个服务定义（7 核心 + backend/frontend/attu/redisinsight）。backend/frontend 容器需构建（Dockerfile），开发时通常直接用 uvicorn + vite 更便捷。可观测性 Langfuse 与 Nginx 反向代理只在生产版 `docker-compose.prod.yml` 中启用。

---

## 6. Complexity Hotspots(新人需要重点理解的)

> 按"面试官最可能追问"排序

| 优先级 | 模块 | 为什么重要 | 怎么学 |
|---|---|---|---|
| 🔴 必懂 | `core/rag/engine.py` 180-270 | 你刚合并的 RAG 5 步流水线 | 打开 sync 后的 inline pre,逐行读 |
| 🔴 必懂 | `agents/router.py` | 三级路由,问"为什么这样设计"必问 | 读 manual Q4 + 打开源码 |
| 🔴 必懂 | `core/keyword_coverage.py` | 在线护栏 vs 离线 RAGAS 的"两个不同东西" | 读 manual Q11 |
| 🟡 重要 | `core/text_to_sql.py` (你刚升级的 492 行) | NL2SQL 五重防护 + 自纠正 | 读 manual Q28-Q30 + Part 7 Q49-Q56 |
| 🟡 重要 | `agents/tool.py` | ReAct 循环 + Loop Breaker | 读 manual Step 2 Tour |
| 🟡 重要 | `core/observability.py` | Langfuse 全链路 trace_id 透传 | 看 observability 配置 |
| 🟢 选懂 | `core/llm_router.py` | 多模型路由(OpenAI/Anthropic) | 看 settings |

---

## 7. 推荐学习路径(给"项目陌生"的你)

### Day 1(今天): 5 分钟走通主链路
1. 打开浏览器 → `http://localhost:8001/docs`(FastAPI 自动生成的 API 文档)
2. 试着发一条 `/api/v1/chat` 请求,看 SSE 9 种事件
3. 打开 `backend/app/api/chat.py:1139 行 SSE 主循环`,数一下 9 种事件

### Day 2: 读懂 RAG 引擎
1. 打开 `core/rag/engine.py:180-270`(已在 manual 中以 inline pre 显示)
2. 跟着 sync 脚本,看每行代码从真实文件来
3. 手动跑 `python scripts/sync_doc_snippets.py`,看 body pre 实时更新

### Day 3: 读懂三级路由
1. 打开 `agents/router.py`
2. 找到"规则 / 语义 / LLM"三段
3. 读 manual Q4 + Q14,看怎么讲

### Day 4+: 用 source-command-learn 模拟面试
- 我扮面试官,问项目问题(从 manual 64 条 Q&A 抽),你答

---

## 8. 配套资源

- `docs/INTERVIEW_STUDY_GUIDE.html` — 面试学习手册(64 条 Q&A，含项目上手启动检查清单与报错速查)
- `docs/QA_AUDIT.md` — Q&A 审计报告
- `scripts/sync_doc_snippets.py` — 代码-文档同步脚本
- `scripts/verify_doc_integrity.py` — 文档完整性验证
- `scripts/check_doc_drift.py` — 引用漂移检测
- `backend/tests/test_text_to_sql.py` — Text-to-SQL 测试(14 个用例)

---

## 9. 如果只读 3 个文件

1. `backend/app/main.py` — 入口(知道怎么启动)
2. `backend/app/api/chat.py` — 主对话流(知道请求怎么进来)
3. `backend/app/core/rag/engine.py:180-270` — RAG 5 步流水线(知道知识怎么检索)

读完这三个,你就懂这个项目 80% 了。
