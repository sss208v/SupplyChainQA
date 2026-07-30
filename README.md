# Supply Chain QA

中文 | [English](README_EN.md)

Supply Chain QA 是一个面向制造业供应链场景的 **RAG + Multi-Agent 智能问答系统**。它不是简单的文档问答 Demo，而是一个可运行、可阅读、可扩展的企业级 AI 应用：统一编排意图路由、多路混合检索、Agent 工具调用、知识图谱、行级权限、并发安全、对话记忆和全链路可观测。

项目重点是 **双链路驱动**：员工用自然语言提问，系统自动路由——知识类问题走 RAG 链路从知识库检索生成答案，业务类问题走 Agent 链路调用工具查询实时数据（库存 / 订单 / 供应商 / 工单）——无需在多个后台系统之间切换。它适合学习企业级 RAG 系统的完整工程实践，也适合作为垂直领域智能问答系统的二次开发基础。

**技术栈**：FastAPI + LangGraph + LangChain + Milvus + Neo4j + Redis + PostgreSQL + Vue3 | llama.cpp 本地部署

## 核心亮点

- **三级级联意图路由**：规则匹配 <1ms（实体编码优先 + 命令词，零 token）→ 语义路由 <10ms（embedding 余弦相似度 + margin 判据，零 token）→ LLM 分类 ~2.5s 兜底，绝大多数请求不消耗 LLM 调用；路由关键词与语义样本外置到 `intent_routes.json` 配置热加载，新增工具只改配置不改代码。

- **多路混合检索 + 自适应 RRF 融合**：Milvus 向量 + BM25 关键词 + Graph RAG 实体关系三路召回，按查询类型自适应加权（精确编码类 BM25 ×1.5，语义提问类向量 ×1.5），再经四层后处理与 BGE-Reranker 精排。

- **Agent 工具调用**：LangChain `bind_tools` + LangGraph `StateGraph`，6 个业务工具，agent↔tools 最多 5 轮收敛，内置 ReAct 死循环熔断器。

- **并发安全的写操作**：Redis 三态幂等（SET NX 原子抢占）+ token 分布式锁（Lua 原子释放）+ 前端审批确认，防止工单重复创建。

- **行级权限 RBAC**：7 部门角色，通过 Milvus ARRAY 列 `security_group` + `array_contains` 过滤实现行级数据隔离，不引入独立权限表。

- **Graph RAG 知识图谱**：Neo4j 存储供应商→物料→订单→仓库实体关系，模板化 Cypher 生成（不让 LLM 直接写 Cypher），支持多跳关联查询。

- **两层语义缓存**：L1 MD5 精确匹配（0ms）+ L2 embedding 相似度 >0.92 语义复用，节省 90%+ API 成本；知识库变更时 L2 通过版本号 INCR 实现 O(1) 主动失效，避免脏缓存与 SCAN 全清。

- **可信度护栏与冲突检测**：生成后校验答案是否忠实于检索上下文；多部门文档对同一指标定义不一致时主动推送冲突提示。

- **流式 SSE + 对话记忆**：Token 级逐字输出，7 种 SSE 事件类型；Redis 滑动窗口记忆 + 后台 LLM 摘要压缩，Redis 故障时自动降级不中断对话。

- **Langfuse 全链路可观测**：路由决策、RAG 检索、工具调用、LLM 生成全部 span 级追踪。

## 项目架构

核心运行链路：

```Plain Text
用户提问 (Vue3 前端)
  -> POST /api/v1/chat/ask (SSE 流式)
  -> 限流中间件（Redis 滑动窗口，故障降级内存）
  -> 三级意图路由（规则 -> 语义 -> LLM 兜底）
  -> RAG 链路:
       查询理解 -> 多路召回（向量 + BM25 + Graph RAG）
       -> 自适应 RRF 融合 -> 四层后处理 -> Reranker 精排
       -> Self-RAG 相关性过滤 -> LLM 流式生成
       -> 可信度护栏 / 冲突检测 / Query 缓存回写
  -> Agent 链路:
       澄清检查 -> RBAC 权限 -> 写操作审批
       -> Redis 幂等抢占 + 分布式锁
       -> LangGraph ReAct（agent <-> tools，最多 5 轮）
  -> SSE 事件流回传（content / sources / tool_call / dag_progress ...）
  -> 对话记忆写入 Redis（pipeline 合并，后台摘要压缩）
```

## 目录结构

```Plain Text
supply-chain-qa/
├── backend/
│   ├── app/
│   │   ├── agents/            # Agent 编排（ToolAgent / LangGraphAgent / 意图路由）
│   │   ├── api/               # FastAPI 路由（chat / knowledge / tool / feedback / evaluate / auth）
│   │   │   └── handlers/      # 意图处理器（rag_answer / tool_call / graph_query）
│   │   ├── core/              # 核心引擎（rag_engine / milvus / redis / neo4j / rate_limiter）
│   │   └── models/            # SQLAlchemy 数据模型
│   ├── tests/                 # 单元测试 + 集成测试（@pytest.mark.integration）
│   ├── scripts/               # 知识入库、benchmark、数据初始化脚本
│   ├── eval/                  # RAG 检索质量评估
│   └── requirements.txt
├── frontend/                  # Vue3 + Element Plus + Pinia（JavaScript，非 TS）
│   └── src/                   # views / stores / api / router
├── knowledge/                 # 知识库文档（7 部门，90+ 篇管理制度与业务数据）
├── models/                    # 本地 GGUF 模型（Qwen3-14B / Qwen2.5 系列）
├── llama.cpp-cuda13/          # llama.cpp CUDA 13 运行时（llama-server.exe）
├── docs/                      # 设计、验证、上手文档
├── docker-compose.yml         # 基础设施容器编排
├── start.ps1 / start-dev.ps1  # 一键启动脚本
└── AGENTS.md                  # AI 编码规则（架构约束与踩坑记录）
```

## 快速启动

### 准备环境

推荐环境：

- Python 3.11

- Node 18+

- Docker Desktop（Milvus / Redis / PostgreSQL / Neo4j）

- 一个 LLM 接口：DeepSeek API、Ollama，或本地 llama.cpp（OpenAI-compatible，端口 8080）

### 配置 .env

复制模板并填入实际值：

```Plain Text
cd backend && cp ../.env.example .env
```

最小可用配置：

```Plain Text
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-api-key
JWT_SECRET=change-me-in-production
```

使用本地模型（Ollama 示例）：

```Plain Text
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
```

完整配置项（RAG 参数、功能开关、Langfuse、CLIP 多模态等）见 [.env.example](.env.example)，调优参数以 `backend/app/config.py` 为唯一真相来源。

### 一键启动

```Plain Text
.\start.ps1
```

自动完成：Docker 基础设施 → 后端 → 前端。开发模式使用 `.\start-dev.ps1`。

### 手动启动

```Plain Text
# 1. 基础设施
docker-compose up -d etcd minio milvus redis postgres neo4j

# 2. 后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 3. 前端
cd frontend && npm install && npm run dev
# -> http://localhost:5173（代理转发到后端 8001）
```

### 知识入库

```Plain Text
cd backend && python scripts/upload_knowledge.py
```

将 `knowledge/` 目录下的文档切块、向量化并写入 Milvus（自动携带 `security_group` 权限标记）。PDF 批量入库使用 `scripts/ingest_pdfs.py`。

### 默认账号

> 由 `DEMO_SEED_USERS=true` 控制，仅用于演示环境。

| 账号      | 密码         | 角色   | 可见范围             |
| --------- | ------------ | ------ | -------------------- |
| admin     | admin123     | 管理员 | 全部                 |
| purchase  | purchase123  | 采购部 | 采购 / 供应商 / 公共 |
| warehouse | warehouse123 | 仓库部 | 库存 / 物流 / 公共   |

更多角色（quality / production / finance / logistics）可通过注册页面或 API 创建。

## 双链路如何工作

Supply Chain QA 的核心特色是 **RAG 检索与 Agent 工具调用的双链路自动分流**。

### RAG 链路：知识类问题

```Plain Text
"采购订单审批流程是什么？"
  -> 意图路由判定 KNOWLEDGE
  -> 多路召回: Milvus 向量 + BM25 + Neo4j 图谱实体
  -> 自适应 RRF 融合（含编码/编号 -> BM25 加权；含"怎么/如何" -> 向量加权）
  -> 四层后处理: 低分过滤 -> Jaccard 去重 -> 冲突检测 -> 可信度护栏
  -> BGE-Reranker 精排 -> Self-RAG 相关性过滤（阈值 0.15，0 命中回退 top-1）
  -> LLM 流式生成 + 来源引用
```

### Agent 链路：业务操作

```Plain Text
"帮我创建一个 MAT-001 缺料的紧急工单"
  -> 意图路由判定 TOOL_CALL
  -> 澄清检查（参数不足主动反问）
  -> RBAC 工具权限检查
  -> 写操作审批（前端确认后执行）
  -> Redis 幂等三态抢占（acquired / pending / completed）
  -> 分布式锁（token + Lua 原子释放）
  -> LangGraph ReAct 执行 create_ticket
  -> 工单 ID: TK-{时间戳}{7位随机数}
```

可用工具（TOOL_REGISTRY 共 11 个）：`query_inventory` / `query_order` / `query_supplier` / `track_logistics` / `create_ticket` / `calculate_reorder_point` / `get_knowledge` / `get_datetime` / `web_search` / `calculator` / `code_interpreter`

核心文件：

```Plain Text
backend/app/agents/router.py           # 三级意图路由（实体优先规则 -> 语义 -> LLM）
backend/app/data/intent_routes.json    # 路由关键词/实体规则/语义样本（热加载）
backend/app/core/rag_engine.py         # RRF 融合 + 四层后处理
backend/app/api/handlers/tool_call.py  # 幂等 + 锁 + 审批链路
backend/app/agents/tool.py             # LangGraph ToolAgent + 死循环熔断
backend/app/core/redis_client.py       # 连接管理 / 对话记忆 / 锁 / 幂等
```

### 防御性工程 (SuperPowers)

- **ReAct 死循环熔断器**：实时监控 Agent 推理路径签名，拦截工具调用死循环并强注入自省指令，确保 100% 收敛。

- **模糊输入自愈归一化**：自动纠正用户拼写错误（O/0 混淆、漏连字符），Graph RAG 与检索链路均支持模糊匹配。

- **Redis 故障降级**：连接断开时对话记忆 / 缓存 / 限流自动降级（不中断主链路），恢复后 10 秒内自动重连。

## 常用命令

### 启动与测试

| 命令                                                                            | 功能                                                     |
| ------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `.\start.ps1`                                                                   | 一键启动全部服务                                         |
| `docker-compose up -d`                                                          | 启动基础设施容器                                         |
| `cd backend && uvicorn app.main:app --reload --port 8001`                       | 后端开发服务器                                           |
| `cd frontend && npm run dev`                                                    | 前端开发服务器 (5173)                                    |
| `cd backend && venv\Scripts\python.exe -m pytest tests -q -k "not integration"` | 后端单元测试（无需 Docker）                              |
| `cd backend && venv\Scripts\python.exe -m pytest tests -q`                      | 全部测试（需 Docker 服务）                               |
| `cd frontend && npm run test:unit`                                              | 前端单元测试                                             |
| `python scripts/run_benchmark.py --mode both`                                   | Agent 工具调用 benchmark（performance / quality 双模式） |

### 测试状态

单元测试约 1051 个用例全部通过（覆盖率 71%+，门槛 70%）。运行前确保 `.env` 中已配置 `JWT_SECRET`；本地匿名演示需设 `REQUIRE_AUTH_CHAT=false`。

## API

| 端点                     | 方法 | 说明                                                  |
| ------------------------ | ---- | ----------------------------------------------------- |
| /api/v1/chat/ask         | POST | 对话（SSE 流式，7 种事件类型）                        |
| /api/v1/tools/call       | POST | Agent 工具调用                                        |
| /api/v1/tools/list       | GET  | 工具列表                                              |
| /api/v1/knowledge/upload | POST | 上传文档（自动切块入库）                              |
| /api/v1/knowledge/list   | GET  | 文档列表                                              |
| /api/v1/auth/login       | POST | 登录（JWT）                                           |
| /health                  | GET  | 全链路健康检查（Milvus / Redis / PostgreSQL / Neo4j） |

交互式文档：启动后访问 http://localhost:8001/docs

## RAG 与 Agent 的分工

| 类型        | 处理内容                             | 数据来源                                                                        |
| ----------- | ------------------------------------ | ------------------------------------------------------------------------------- |
| RAG 链路    | 制度、流程、规范类知识问答           | knowledge/ 文档库（Milvus + BM25 + Neo4j）                                      |
| Agent 链路  | 库存、订单、供应商实时查询与工单创建 | PostgreSQL 业务库（工具调用）                                                   |
| Text-to-SQL | 自然语言统计分析                     | PostgreSQL（五层安全防护：仅 SELECT / 表白名单 / 禁用关键词 / 行数限制 / 超时） |

## Docker 运行

基础设施编排（开发）：

```Plain Text
docker-compose up -d
```

包含服务：Milvus（etcd + minio）、Redis、PostgreSQL、Neo4j、Langfuse。

前后端均提供 Dockerfile（`backend/Dockerfile`、`frontend/Dockerfile`）用于容器化构建。

## 重要数据路径

| 数据         | 路径                                                                 |
| ------------ | -------------------------------------------------------------------- |
| 知识库源文档 | knowledge/                                                           |
| 上传文档     | backend/uploads/                                                     |
| 向量数据     | Milvus collection `supply_chain_qa_docs`（含 security_group 权限列） |
| 对话记忆     | Redis `scqa:chat:*` / `scqa:chat_summary:*`（滑动窗口 + 摘要）       |
| 幂等与锁     | Redis `idempotent:tool:*` / `lock:tool:*`                            |
| 业务数据     | PostgreSQL（端口 15432）                                             |
| 实体图谱     | Neo4j（bolt 端口 17687）                                             |
| 本地模型     | models/*.gguf（llama.cpp 加载）                                      |

## 关键文档

| 文档                                                     | 用途                                      |
| -------------------------------------------------------- | ----------------------------------------- |
| [AGENTS.md](AGENTS.md)                                   | AI 编码规则：架构约束、踩坑记录、交付流程 |
| [docs/DESIGN.md](docs/DESIGN.md)                         | 系统设计文档                              |
| [docs/ONBOARDING.md](docs/ONBOARDING.md)                 | 新成员上手指南                            |
| [docs/VERIFICATION_GUIDE.md](docs/VERIFICATION_GUIDE.md) | 功能验证手册                              |
| [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)       | 项目背景与上下文                          |

## 适合如何使用

Supply Chain QA 适合：

- 学习企业级 RAG 系统的完整工程链路：混合检索、RRF 融合、重排、后处理、护栏。

- 学习 LangGraph Agent 的工具调用编排、审批流与并发安全设计（幂等 + 分布式锁）。

- 学习 Milvus 行级权限（RBAC）、Graph RAG 与 Text-to-SQL 的落地方式。

- 作为垂直领域（制造、物流、金融）智能问答系统的二次开发底座。

- 全本地化部署实践：llama.cpp + GGUF 模型 + 自托管基础设施，数据不出内网。

如果只看一个核心点：Supply Chain QA 是一个把知识检索和业务操作统一在自然语言入口下的 **双链路供应链智能问答系统**。
