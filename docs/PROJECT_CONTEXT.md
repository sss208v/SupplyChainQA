# Supply Chain QA — 项目上下文文件

> 本文件为 AI 助手快速了解项目而设计。在新对话中告知 AI 阅读此文件，即可立即掌握项目全貌。
>
> 项目路径: `C:\Users\sss208\Desktop\agent\supply-chain-qa\`
>
> 最后更新: 2026-06-13

---

## 一、项目概述

Supply Chain QA 是面向制造业供应链的企业级智能问答系统，核心能力是 RAG（检索增强生成）+ Multi-Agent 工具调用。系统支持多部门角色权限控制、流式问答、知识库管理、智能评估等功能。

**目标用户**: 面试时作为主力项目展示，面向 AI/大模型应用工程师岗位。

---

## 二、技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | FastAPI + Uvicorn (异步) |
| LLM 服务 | llama.cpp 本地部署 Qwen2.5-1.5B (端口 8080)，兼容 OpenAI API |
| LLM 编排 | LangChain >=0.3 + LangGraph >=0.2 |
| 向量数据库 | Milvus 2.3.4 (standalone) |
| 图数据库 | Neo4j 5 Community |
| 缓存/消息 | Redis 7 |
| 关系数据库 | PostgreSQL 15 (元数据) + SQLite (应用数据) |
| Embedding | BAAI/bge-base-zh-v1.5 (512维) |
| Reranker | BAAI/bge-reranker-v2-m3 |
| 前端 | Vue 3 + Element Plus + Pinia (JavaScript，非 TypeScript) |
| 构建工具 | Vite 8 |
| 前端测试 | Vitest 3 + @vue/test-utils |
| 后端测试 | pytest 8 + pytest-asyncio |
| 容器化 | Docker Compose (10个服务) |
| 可观测性 | Langfuse (span级追踪) |
| 流式传输 | SSE (Server-Sent Events)，7种事件类型 |

---

## 三、核心架构

### 3.1 RAG 检索管线（多路混合检索 → RRF 融合）

```
用户查询
  ↓
三级意图路由: 规则匹配(<1ms) → 语义路由(<10ms) → LLM分类(~2.5s)
  ↓
多路混合检索（3 路召回）:
  1. Milvus 向量检索 (BGE Embedding)
  2. BM25 关键词检索 (rank_bm25)
  3. Graph RAG 实体关系检索 (Neo4j 2-hop)
  ↓
查询类型感知 RRF 权重融合 (K=60)
  ↓
四层后处理:
  1. 低分过滤
  2. Jaccard 去重
  3. 冲突检测 (多源文档冲突 → SSE通知)
  4. 可信度护栏 (Faithfulness Guardrails)
  ↓
BGE-Reranker 精排
  ↓
Self-RAG 相关性过滤（借鉴 Asai 2023 思想，LLM-as-Judge 替代 reflection token）
  ↓
LLM 生成 → SSE 流式输出
```

### 3.2 Multi-Agent 工具调用（LangGraph StateGraph）

- 架构: LangGraph `StateGraph` + LangChain `bind_tools`
- 3种 Agent 实现: ToolAgent (默认, ReAct) / LangGraphAgent / LangChainAgent
- 6个业务工具: `query_inventory`, `query_order`, `create_ticket`, `get_datetime`, `get_knowledge`, `query_supplier`
- 收敛: 最多 5 轮迭代，20次跨工具基准测试 100% 成功率
- 安全: ReAct 死循环熔断器（实时监控推理路径签名，自动注入自省指令）

### 3.3 行级 RBAC 权限控制

- 基于 Milvus `security_group` ARRAY 列
- 查询时通过 `array_contains` 过滤，实现差异化数据可见性
- 7个部门角色: admin, purchase, warehouse, production, quality, finance, logistics
- 预设账号: admin/admin123, purchase/purchase123, warehouse/warehouse123

### 3.4 两层语义缓存

- L1: MD5 精确匹配 (命中率最高)
- L2: Embedding 余弦相似度 > 0.92
- TTL: 600秒，最大200条缓存
- 效果: 节省 90% 以上 LLM 调用成本

### 3.5 Text-to-SQL

- 自然语言转 PostgreSQL 查询
- 5层安全防护: 白名单表、列名校验、SQL注入检测、只读模式、结果行数限制

### 3.6 Graph RAG (Neo4j)

- 存储供应链实体关系 (供应商-物料-订单-部门)
- 模板化 Cypher 生成（避免 LLM 直接生成 Cypher 产生幻觉）
- 图检索结果与向量检索通过 alpha=0.7, beta=0.3 权重融合

---

## 四、项目规模与指标

| 指标 | 数值 |
|------|------|
| 后端 Python 源文件 | 66 个 |
| 后端代码行数 | 14,584 行 |
| 后端测试文件 | 40 个 (39 test + 1 conftest) |
| 后端测试用例 | 728 个 (684 单元 + 44 集成) |
| 前端源文件 | 29 个 |
| 前端代码行数 | 7,025 行 |
| 前端测试用例 | 62 个 (Vitest) |
| 知识库文档 | 94 份 Markdown，覆盖 7 个部门 |
| Docker 服务 | 10 个 (dev) / 10+nginx (prod) |
| 设计文档 | 32 个 (plan/) |
| 工具脚本 | 32 个 (scripts/) |

### RAGAS 评估得分

| 指标 | 分数 |
|------|------|
| Context Precision | 0.82 |
| Faithfulness | 0.88 |
| Answer Relevance | 0.85 |
| Context Recall | 0.84 |

---

## 五、目录结构

```
supply-chain-qa/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── main.py            # 应用入口，FastAPI 实例 + 生命周期
│   │   ├── config.py          # Pydantic Settings 配置
│   │   ├── api/               # API 路由层
│   │   │   ├── chat.py        # 聊天 SSE 流 (1116行，最大文件)
│   │   │   ├── knowledge.py   # 知识库管理 API
│   │   │   ├── evaluate.py    # RAG 评估 API
│   │   │   ├── auth.py        # 认证 API
│   │   │   └── chat_helpers.py
│   │   ├── agents/            # Agent 层
│   │   │   ├── rag.py         # RAG Agent (634行)
│   │   │   ├── router.py      # 三级意图路由（规则/语义样本外置 data/intent_routes.json）
│   │   │   ├── orchestrator.py # Agent 编排器
│   │   │   ├── base_agent.py  # Agent 基类
│   │   │   └── _legacy/       # 旧版 Agent 实现
│   │   ├── core/              # 核心业务逻辑
│   │   │   ├── tool_engine.py # 工具调用引擎 (808行)
│   │   │   ├── rag/engine.py  # RAG 引擎 (544行)
│   │   │   ├── milvus_client.py # Milvus 客户端
│   │   │   ├── neo4j_client.py  # Neo4j 客户端
│   │   │   ├── redis_client.py  # Redis 客户端
│   │   │   ├── semantic_cache.py # 语义缓存（知识库版本号 O(1) 失效）
│   │   │   ├── intent_routes.py  # 意图路由配置加载器（mtime 热加载 + 工具名校验）
│   │   │   ├── self_rag.py    # Self-RAG 实现
│   │   │   ├── text_to_sql.py # Text-to-SQL
│   │   │   ├── graph_engine.py # 图引擎
│   │   │   ├── faithfulness.py # 可信度护栏
│   │   │   ├── data_filter.py # 数据过滤
│   │   │   ├── data_preprocess.py # 数据预处理
│   │   │   └── evaluator.py   # 在线评估
│   │   ├── models/            # 数据模型
│   │   └── data/              # SQLite + 初始化脚本 + intent_routes.json 路由配置
│   ├── tests/                 # 728 个测试用例
│   ├── eval/                  # RAGAS 评估脚本
│   └── requirements.txt
├── frontend/                   # Vue 3 前端
│   └── src/
│       ├── api/               # HTTP 请求封装
│       ├── views/             # 页面视图 (Chat/Dashboard/Knowledge/Tools/Evaluate/Login)
│       ├── stores/            # Pinia 状态管理
│       ├── components/        # 公共组件
│       └── router/            # Vue Router
├── knowledge/                  # 知识库文档 (94份 Markdown, 按部门前缀命名)
├── models/                     # 本地 LLM 模型 (GGUF 格式)
│   ├── qwen2.5-0.5b-instruct-q4_k_m.gguf
│   ├── qwen2.5-1.5b-instruct-q4_k_m.gguf
│   └── qwen2.5-7b-instruct-q4_k_m (2个分片)
├── scripts/                    # 工具脚本 (demo/benchmark/upload/e2e)
├── plan/                       # 设计文档 (32份)
├── docs/                       # 文档 (架构图/面试指南/学习路径)
├── deploy/                     # 部署配置 (nginx/certs/脚本)
├── llama.cpp-cuda13/          # llama.cpp 本地推理服务
├── docker-compose.yml          # 开发环境 (10个服务)
├── docker-compose.prod.yml     # 生产环境 (+ nginx)
├── README.md                   # 中文 README
└── README_EN.md                # 英文 README
```

---

## 六、API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/chat/ask` | POST | 聊天 (SSE 流式) |
| `/api/v1/tools/call` | POST | Agent 工具调用 |
| `/api/v1/knowledge/upload` | POST | 上传文档 |
| `/api/v1/knowledge/list` | GET | 文档列表 |
| `/api/v1/auth/login` | POST | 登录 |
| `/api/v1/tools/list` | GET | 工具列表 |
| `/api/v1/evaluate/run` | POST | 运行 RAG 评估 |
| `/health` | GET | 健康检查 |

---

## 七、Docker 服务 (docker-compose.yml)

| 服务 | 镜像 | 端口 | 用途 |
|------|------|------|------|
| backend | 自建 (python:3.11-slim) | 8001 | FastAPI 后端 |
| frontend | 自建 (nginx) | 3000 | Vue3 前端 |
| milvus | milvusdb/milvus:v2.3.4 | 19530 | 向量数据库 |
| redis | redis:7-alpine | 6379 | 缓存 + 会话 |
| postgres | postgres:15-alpine | 15432 | 元数据存储 |
| neo4j | neo4j:5-community | 7474/7687 | 图数据库 |
| etcd | quay.io/coreos/etcd:v3.5.5 | - | Milvus 元数据 |
| minio | minio/minio | 9000/9001 | Milvus 对象存储 |
| attu | zilliz/attu:v2.3.4 | 8000 | Milvus GUI |
| redisinsight | redis/redisinsight | 5541 | Redis GUI |

---

## 八、配置要点 (config.py / .env)

- **LLM**: 默认通过 llama.cpp 本地部署，兼容 OpenAI API，`DEEPSEEK_BASE_URL=http://llama-cpp:8080/v1`
- **Embedding**: `BAAI/bge-base-zh-v1.5`，512维，Docker 构建时预下载
- **RAG 参数**: chunk_size=256, overlap=128, RRF_K=60, top_k 按路由动态调整
- **Self-RAG**: 默认启用，阈值 0.15
- **语义缓存**: 启用，余弦阈值 0.92，TTL 600s，上限 200 条
- **Agent**: 默认 react (ReAct)，可选 langgraph
- **Neo4j 融合权重**: alpha=0.7 (向量), beta=0.3 (图)
- **Demo 模式**: 无 LLM 时降级为规则应答

---

## 九、测试运行命令

```bash
# 后端单元测试 (不需要 Docker 服务，约30秒)
cd backend && venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 后端全部测试 (需要 Docker 服务运行，约60秒)
cd backend && venv\Scripts\python.exe -m pytest tests -q

# 前端测试
cd frontend && npm run test:unit

# 跳过项: Redis/Neo4j 未运行时部分集成测试自动 skip (约5个)
```

---

## 十、已修复的问题与当前状态

### 已修复的 Bug

1. **knowledge.py 路径错误**: `/ingest` 端点指向错误的 scripts 目录，已修正路径
2. **Redis 连接状态不一致**: `connect()` 失败时未清空 `_pool`，导致 `is_connected` 误报，已修复
3. **工单 ID 碰撞**: `tool_engine.py` 中 ticket ID 后缀从5位扩展到7位，防止 UNIQUE 约束失败
4. **前端死代码**: 移除了不存在的 `/api/v1/chat/completions` 调用和未使用的 `sendMessageSync`
5. **静默异常**: 为 `milvus_client.py`, `semantic_cache.py`, `text_to_sql.py`, `evaluator.py`, `langgraph_agent.py` 中的静默 except 添加了日志

### 当前状态

- 728 个后端测试全部通过 (0 failed)
- 62 个前端测试全部通过
- README 中英文版本已同步（功能列表14项、llama.cpp 本地部署、Docker 10服务、正确的 API 路径）
- CI badge 已修复为静态 badges
- docker-compose.prod.yml 已补充 LLM 配置环境变量
- LangChain 弃用警告已抑制

---

## 十一、面试要点速查

### 亮点数据（面试高频问题）

- "多路混合检索": Milvus 向量 + BM25 + Graph RAG 三路召回，RRF 融合，BGE-Reranker 精排
- "RRF 融合公式": score = Σ 1/(K + rank_i)，K=60 为平滑常数
- "Agent 收敛": 最多 5 轮，20 次基准 100% 成功率
- "语义缓存": 两层（MD5 + 余弦 0.92），节省 90%+ 调用
- "权限控制": Milvus ARRAY 列 + array_contains 行级 RBAC
- "测试覆盖": 728 个测试，0 失败
- "RAGAS 评估": Faithfulness 0.88（最高），Context Precision 0.82

### 技术决策理由（面试追问）

| 决策 | 理由 |
|------|------|
| 为什么用 llama.cpp 而非 API？ | 企业数据敏感，本地部署保证数据不出网 |
| 为什么 RRF 而非加权求和？ | RRF 不依赖分数归一化，对不同检索器的分数尺度鲁棒 |
| 为什么用 Milvus ARRAY 做 RBAC？ | 避免维护独立权限表，检索与过滤在同一次查询中完成 |
| 为什么用模板化 Cypher？ | LLM 直接生成 Cypher 容易幻觉，模板化保证查询正确性 |
| 为什么 Self-RAG？ | 固定 top-k 在简单问题上浪费 token，Self-RAG 自适应决定是否检索 |

### 常见面试问题预备

1. **介绍你的 RAG 系统**: 从三级意图路由 → 多路召回 → RRF 融合 → 四层后处理 → Self-RAG 相关性过滤 完整链路
2. **Agent 是怎么工作的**: LangGraph StateGraph + bind_tools，ReAct 模式，熔断器防死循环
3. **怎么保证回答质量**: Faithfulness Guardrails + Self-RAG 反思 + RAGAS 评估
4. **权限怎么控制**: Milvus ARRAY 行级 RBAC，不是传统的 RBAC 表
5. **测试怎么做的**: 728 个测试，单元 vs 集成分离，aiosqlite 异步测试需注意事件循环生命周期

---

## 十二、关键文件索引

| 文件 | 说明 | 面试相关度 |
|------|------|-----------|
| `backend/app/agents/rag.py` | RAG Agent 核心实现 | 高 |
| `backend/app/core/rag/engine.py` | RAG 引擎 (检索+融合+后处理) | 高 |
| `backend/app/core/tool_engine.py` | 工具调用引擎 | 高 |
| `backend/app/api/chat.py` | SSE 聊天流 (最大文件) | 高 |
| `backend/app/agents/router.py` | 三级意图路由 | 高 |
| `backend/app/core/semantic_cache.py` | 语义缓存 | 中 |
| `backend/app/core/self_rag.py` | Self-RAG | 高 |
| `backend/app/core/faithfulness.py` | 可信度护栏 | 中 |
| `backend/app/core/milvus_client.py` | Milvus 操作 | 中 |
| `backend/app/core/neo4j_client.py` | Graph RAG | 中 |
| `backend/app/core/text_to_sql.py` | Text-to-SQL | 中 |
| `backend/app/config.py` | 全局配置 | 低 |
| `backend/app/main.py` | 应用启动 + 生命周期 | 低 |
| `CLAUDE.md` | AI 编码规则文件（30+ 条踩坑规则） | 高 |
| `docs/PROJECT_METRICS.md` | 项目指标 | 中 |

> **面试资料已独立存放至 `D:\2026\面试准备\`**（25 个文件），包括面试指南、速查手册、演示脚本、AI 编码话术、学习路径等，不与项目代码混在一起。

---

*本文件随项目持续更新。修改日期: 2026-06-14*
