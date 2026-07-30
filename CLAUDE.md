# Supply Chain QA — AI 编码规则

> 本文件供 Claude Code / Cursor / Copilot 等 AI 编码工具读取。
> 只写 AI 容易猜错、代码里读不出来的规则。每条规则都经过实际踩坑验证。

## 技术栈版本（不要猜，看这里）

- Python 3.11（不是 3.12+，`asyncio` 部分 API 有差异）
- FastAPI + Uvicorn（不是 Flask/Django）
- Vue 3 + Element Plus + Pinia，**JavaScript，不是 TypeScript**
- LangChain >=0.3 + LangGraph >=0.2（API 与 0.1/0.2 差异很大，不要用旧 API）
- Milvus 2.3.4（standalone 模式，不是 Zilliz Cloud）
- Redis 7（用 `redis[hiredis]` 异步客户端，不是 `aioredis`）
- PostgreSQL 15（通过 SQLAlchemy async + asyncpg）
- SQLite 通过 `aiosqlite`（异步连接，注意事件循环生命周期）
- llama.cpp 本地部署（兼容 OpenAI API，端口 8080，不要用 DeepSeek/Ollama 专属 API）

## 常用命令

```bash
# 后端测试（单元测试，不需要 Docker）
cd backend && venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 后端全部测试（需要 Docker 服务）
cd backend && venv\Scripts\python.exe -m pytest tests -q

# 前端测试
cd frontend && npm run test:unit

# 启动后端开发服务器
cd backend && uvicorn app.main:app --reload --port 8001

# Docker 全部启动
docker-compose up -d
```

## 架构约束（违反会导致系统不一致）

### 后端

- **异步优先**：所有数据库操作必须用 async/await。不要用同步 `sqlite3` 或 `psycopg2`，用 `aiosqlite` 和 `asyncpg`
- **aiosqlite 事件循环**：每个测试函数必须自己创建 `_db` 实例并在函数内 `close()`，不要共享模块级连接（`asyncio.run()` 每次创建新 event loop）
- **Redis 连接状态**：`redis_client.py` 的 `connect()` 失败时必须清空 `self._pool = None`，否则 `is_connected` 会误报
- **Milvus RBAC**：权限控制通过 Milvus `security_group` ARRAY 列 + `array_contains` 过滤，不要创建独立的权限表
- **意图路由配置外置**：路由关键词/实体规则/语义样本在 `backend/app/data/intent_routes.json`（`core/intent_routes.py` 按 mtime 热加载），不要把关键词表硬编码回 `agents/router.py`；语义样本变更后需调 `POST /api/v1/knowledge/router/reload`（admin）重建 embedding
- **语义缓存失效**：`semantic_cache.invalidate()` 是 `INCR scqa:kb:version` 的 O(1) 版本号失效（旧条目 lookup 时惰性清理），不要改回 SCAN 全清（`purge()` 仅作运维兜底）
- **Graph RAG Cypher**：Neo4j 查询必须用模板化生成（`neo4j_client.py` 中的模板），不要让 LLM 直接生成 Cypher
- **SSE 流式**：聊天接口 `/api/v1/chat/ask` 返回 SSE（7 种事件类型），不要改成 WebSocket 或普通 JSON
- **工单 ID 格式**：`TK-{时间戳}{7位随机数}`，后缀必须 >=7 位，否则高并发下 UNIQUE 约束会碰撞
- **LangChain 弃用警告**：`main.py` 已抑制 `PendingDeprecationWarning`（来自 langgraph），不要移除

### 前端

- **JavaScript 不是 TypeScript**：不要添加 `.ts` 文件或类型注解
- **Element Plus 优先**：UI 组件优先用 Element Plus，不要引入其他 UI 库
- **Pinia 状态管理**：状态用 Pinia store，不要用 Vuex 或 composables 替代
- **API 封装**：所有 HTTP 请求放在 `src/api/` 下，通过 `request.js` 统一封装
- **路由**：页面路由在 `src/router/index.js`，6 个页面（Login/Dashboard/Chat/Knowledge/Tools/Evaluate）

## 项目坑点（踩过才写的）

### 路径问题

- `knowledge.py` 的 `/ingest` 端点调用 scripts 目录时，路径是 `os.path.join(os.path.dirname(__file__), "..", "..", "scripts")`（两层 `..`，因为 api/ 在 app/ 下面）
- 不要硬编码绝对路径，用 `os.path.dirname(__file__)` 相对计算

### 异步测试陷阱

- `pytest-asyncio` 的 `asyncio_mode = auto` 已配置，但每个 async 测试函数跑在独立的 event loop 中
- 不要在 async 测试中调用 `asyncio.run()`（会创建嵌套 event loop）
- `aiosqlite` 连接必须在同一个 event loop 中创建和关闭
- 如果一个测试需要多个 async 操作，写成一个 `async def` 函数而不是多次 `asyncio.run()`

### Redis 连接陷阱

- `redis_client.py` 的 `connect()` 方法：先设置 `_pool`，再 `ping()`。如果 `ping()` 失败且不清空 `_pool`，`is_connected` 属性会返回 True（bug 已修复，改代码时注意不要回退）

### Milvus 操作

- Milvus 集合有 `security_group` ARRAY 字段，插入时必须提供（否则 RBAC 失效）
- `get_total_count()` 和 `get_distinct_doc_count()` 有 try/except，异常时必须 `logger.warning`，不要静默吞掉

### LangChain/LangGraph

- LangChain 0.3 的 `bind_tools()` 要求 tool schema 是 OpenAI function calling 格式
- LangGraph `StateGraph` 的 node 函数必须返回 dict（更新 state），不能返回 None
- `_legacy/` 目录是旧版实现，不要修改或引用

## 新增文件放置规则

| 类型 | 位置 | 命名 |
|------|------|------|
| API 路由 | `backend/app/api/` | 功能名.py |
| Agent | `backend/app/agents/` | 功能名.py |
| 核心逻辑 | `backend/app/core/` | 功能名.py |
| 测试 | `backend/tests/` | test_功能名.py |
| 前端页面 | `frontend/src/views/页面名/` | index.vue |
| 前端 Store | `frontend/src/stores/` | 功能名.js |
| 前端 API | `frontend/src/api/` | 功能名.js |
| 知识库文档 | `knowledge/` | SC-{部门}-{序号}.md |
| 意图路由配置 | `backend/app/data/` | intent_routes.json（关键词/实体规则/语义样本，改配置不改代码） |

## 测试规范

- 单元测试不需要 Docker 服务，mock 外部依赖（Milvus/Redis/Neo4j/LLM）
- 集成测试标记 `@pytest.mark.integration`，需要 Docker 服务运行
- 测试函数命名：`test_{功能}_{场景}`
- 异步测试：直接用 `async def test_xxx()`，框架会自动处理 event loop
- 新增功能必须附带测试，至少覆盖：正常路径、空输入、权限不足、并发

## 禁止事项

- 不要把前端改成 TypeScript
- 不要引入新的 UI 框架（保持 Element Plus）
- 不要用 `asyncio.run()` 包裹已有 async 函数
- 不要删除 `main.py` 中的 LangChain 弃用警告抑制
- 不要修改 `_legacy/` 目录
- 不要创建独立的权限表（用 Milvus ARRAY）
- 不要让 LLM 直接生成 Cypher（用模板）
- 不要硬编码 LLM API URL（通过 `config.py` 配置）
- 不要在 except 中静默 `pass`（至少加 `logger.warning`）
- 不要把聊天接口从 SSE 改成 WebSocket
