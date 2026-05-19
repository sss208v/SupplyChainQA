# SmartQA Pro — 供应链智能问答系统

面向制造业供应链场景的 RAG + Multi-Agent 智能问答系统。员工用自然语言提问，系统从知识库检索答案或调用业务系统查询实时数据——双链路驱动，无需切换多个后台。

**技术栈**: FastAPI + LangGraph + LangChain + Milvus + Redis + Vue3 | DeepSeek API

## 核心能力

### 意图路由（三级级联）

1. 规则匹配 <1ms：关键词/正则，零 token
2. 语义路由 <10ms：embedding 余弦相似度，零 token
3. LLM 分类 ~2.5s：兜底复杂语义

### RAG 检索链

混合检索（Milvus 向量 + BM25 关键词）→ 自适应 RRF 权重融合 → 四层后处理（低分过滤 / Jaccard 去重 / 冲突检测）→ BGE-Reranker 精排。知识库覆盖 7 个部门。

**自适应 RRF**: 精确查询（含编码/编号）→ BM25 权重 ×1.5，语义查询（含怎么/如何）→ 向量权重 ×1.5。

### Agent 工具调用

LangChain `bind_tools` + LangGraph `StateGraph`。三选一 Agent：ToolAgent（LangGraph ReAct，默认）/ LangGraphAgent（Router→Tool→Observe→Decide）/ LangChainAgent（AgentExecutor 备选）。6 个工具，agent↔tools 最多 5 轮收敛。

工具: `query_inventory` / `query_order` / `create_ticket` / `get_datetime` / `get_knowledge` (RAG) / `query_supplier`

Benchmark: 20 次跨工具查询，100% 成功率。数据见 `backend/eval/benchmark_report.json`。

### 行级权限

7 部门角色（admin / purchase / warehouse / quality / production / finance / logistics），Milvus ARRAY 列 `security_group` + `array_contains` 过滤。

### 多源冲突检测

供应链多部门文档可能对同一指标有不同定义。正则提取实体+数值 → 同实体不同值 → SSE 推送 `conflict_detected` 事件 → 冲突上下文追加到 LLM prompt。

### 流式 SSE + 操作审批

Token 级逐字输出，7 种 SSE 事件类型。写操作执行前发送审批请求，前端确认后执行。

## 快速开始

```powershell
.\demo_start.ps1
```

一键启动 Docker 基础设施 + 后端 + 前端。

### 手动启动

```bash
# 1. 基础设施
docker-compose up -d etcd minio milvus redis postgres neo4j

# 2. 后端 (Python 3.10+)
cd backend && cp ../.env.example .env
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 3. 前端 (Node 18+)
cd frontend && npm install && npm run dev
# → http://localhost:3000
```

## 默认账号

> 与 `backend/app/main.py` 实际初始化逻辑一致。

| 账号 | 密码 | 角色 | 可见范围 |
|------|------|------|----------|
| admin | admin123 | 管理员 | 全部 |
| purchase | purchase123 | 采购部 | 采购/供应商/公共 |
| warehouse | warehouse123 | 仓库部 | 库存/物流/公共 |

> 更多角色（quality / production / finance / logistics）可通过注册页面或 API 创建。

## 测试状态

```bash
cd backend && pytest tests/ -q -k "not integration"
# 84 passed, 4 skipped（3 个 Neo4j 集成测试 + 1 个 LangGraph 集成测试需真实服务/LLM）
#
# 运行条件：pytest-asyncio >= 0.23, asyncio_mode = auto（已配置在 pytest.ini）
# 完整测试需：Docker 服务运行（Milvus/Redis/PostgreSQL/Neo4j）
```

## 项目结构

```
supply-chain-qa/
├── backend/
│   ├── app/
│   │   ├── agents/           # Agent 编排
│   │   │   ├── tool.py       # LangGraph ToolAgent (221行)
│   │   │   ├── rag.py        # RAG Agent
│   │   │   └── router.py     # 三级意图路由 (352行)
│   │   ├── api/
│   │   │   ├── chat.py       # SSE 流式主循环 (1139行)
│   │   │   ├── knowledge.py  # 知识库管理
│   │   │   └── auth.py       # RBAC 认证
│   │   └── core/
│   │       ├── rag_engine.py # RRF融合+四层后处理 (753行)
│   │       ├── milvus_client.py  # 行级权限 (439行)
│   │       ├── tool_metrics.py   # 工具调用指标 (88行)
│   │       ├── retry.py      # 流式重试 (184行)
│   │       └── ...
│   └── requirements.txt
├── frontend/                 # Vue3 + Element Plus + Pinia (JavaScript)
├── docs/                     # 面试手册 + 架构图
│   ├── interview-showcase.html   # 面试展示页
│   └── interview-coach.html      # AI 面试陪练
├── plan/                     # 设计文档
├── docker-compose.yml        # 8 服务容器编排
└── README.md
```

## 关键文档

| 文档 | 用途 |
|------|------|
| [面试展示页](docs/interview-showcase.html) | 面试时打开，系统架构+亮点+FAQ |
| [AI 面试陪练](docs/interview-coach.html) | AI 模拟面试，逐题评分 |
| [面试指南](docs/INTERVIEW_GUIDE.md) | 演示流程+话术+追问预判 |
| [学习路线](docs/ai_engineer_learning_path.md) | AI 工程师渐进学习路径 |

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| /api/v1/chat | POST | 对话（支持 SSE 流式） |
| /api/v1/tools/call | POST | Agent 工具调用 |
| /api/v1/knowledge/upload | POST | 上传文档 |
| /api/v1/knowledge/list | GET | 文档列表 |
| /api/v1/auth/login | POST | 登录 |
| /api/v1/tools/list | GET | 工具列表 |
