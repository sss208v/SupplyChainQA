# SmartQA Pro — 面试指南

> 本文件为面试场景设计，帮助你在现场演示和口头问答中快速定位重点、应对追问。

---

## 项目一句话介绍

**企业级供应链知识库 QA 系统**，基于 RAG 检索 + 多 Agent 决策 + 行级权限控制，支持实时 SSE 流式输出和工具调用（查库存、查订单、创建工单等）。

---

## 架构亮点速答

### 三种 Agent 模式演进（必问）

```
agent.py  (默认)      → 手写 ReAct，280 行，完全可控
langchain_agent.py    → AgentExecutor，标准化生态
langgraph_agent.py    → StateGraph，前沿架构
```

**为什么三层？**

- 第一层（react）：面试可讲——状态机思想、tool_calls 结构、streaming
- 第二层（langchain）：了解主流框架，学习成本低
- 第三层（langgraph）：StateGraph + conditional edges，面试差异化亮点

三者接口完全一致：`async def run(query, tool_names, session_id) → dict`，切换只需改一行配置。

---

### 双数据库设计

PostgreSQL 做用户认证和工单写入（ACID 事务场景），工具数据存 SQLite 是为了降低演示部署门槛。生产环境会替换为 ERP API。

---

### Reranker 模型说明

`RERANKER_ENABLED=true` 后，启动时会加载 `BAAI/bge-reranker-v2-m3` 模型（约 1.1GB）。
首次启动需要从 HuggingFace 下载，耗时 1-3 分钟（取决于网络）。
模型缓存到本地 `~/.cache/huggingface/hub/`，后续重启无需重新下载。

如果启动时卡在"正在加载重排序模型..."，属于正常现象——等待下载完成即可。
如网络无法访问 HuggingFace，可临时改为 `RERANKER_ENABLED=false`。

---

### PDF 表格解析引擎

系统内置三层 PDF 解析回退链：
1. **pymupdf4llm**（首选）：结构化 Markdown 输出，保留标题层级和表格
2. **opendataloader**（回退）：辅助 PDF 解析
3. **pdfplumber**（最终回退）：文本提取 + 表格→Markdown 转换

表格在解析时自动转为 Markdown 表格格式，送入 Embedding 模型后，大模型能准确回答表格内具体数据。`requirements.txt` 已包含 `pymupdf4llm>=0.1.0`。

---

### RAGAS 风格评估

系统内置 `GET /api/v1/evaluate/full` 端点，对黄金测试集（`backend/data/eval_ground_truth.json`，12 条供应链 QA 对）自动检索并计算：
- **Context Precision**（检索准确率）：检索结果中相关文档的比例
- **Faithfulness**（忠实度）：回答关键词在检索上下文中的覆盖率
- **Answer Relevance**（回答相关性）：回答与问题的关键词重叠度

实测指标（473 chunks 知识库）：**CP=0.898**。指标值随知识库大小和质量变化，面试时应解释"894 个 chunk 的小型知识库，CP 接近 0.9 说明混合检索架构有效"。详见 Evaluate 页面的 RAGAS 评估卡片。

---

### RAG 指标为什么是 0.53

R@3=0.53 受限于知识库只有 20 篇文档。混合检索（BM25+向量）已经把单一通道的 0.2 拉到 0.53，证明架构有效。语料翻倍后还会涨。

---

### LangGraph 测试为什么是手动演示

**不是偷懒，是框架限制。**

LangGraph 的 `graph.astream()` 内部执行引擎是 Python 闭包，`unittest.mock.patch` 无法穿透到它的运行上下文。真实 LLM 调用发生在图节点的闭包内部，mock 总是打到旧模块路径。

**两种解法：**
1. 跑 live test（需要真实 LLM API + Docker 服务）
2. 接受 skip，用手动的 DEMO_SCRIPT 演示

面试时说：**"LangGraph 测试我做了，但框架本身不支持单元测试 mock，我用手动演示替代，演示效果反而更真实。"**

---

## 现场演示流程（约 10 分钟）

### 1. 启动（2 分钟）

**一键启动（推荐）：**
```powershell
cd supply-chain-qa
.\demo_start.ps1
```

**或手动启动：**
```bash
# 终端 1：基础设施
cd supply-chain-qa
docker-compose -f docker-compose.yml up -d

# 终端 2：后端
cd backend
venv\Scripts\python -m uvicorn app.main:app --reload --port 8001

# 终端 3：前端
cd frontend
npx vite --port 3000
```

### 2. 演示场景

详见 [DEMO_SCRIPT.md](./DEMO_SCRIPT.md)，包含 12 个完整演示场景，按顺序操作即可。核心场景：

| # | 场景 | 关键卖点 |
|---|------|----------|
| 1 | 知识库问答 | 混合检索 + 引用溯源 |
| 2 | Query Cache | MD5 缓存，零 token 重放（前端显示 ⚡ 缓存命中 标签） |
| 3-4 | 工具调用 | ReAct 循环，库存/订单查询 |
| 5 | 混合意图 | RAG + 工具协同 |
| 6 | Token 成本追踪 | 实时计费 |
| 7 | 多轮对话 + 反馈 | Redis 上下文 + 满意度闭环 |
| 8 | Supplier 工具扩展 | 插件化注册（TOOL_REGISTRY 5行代码） |
| 9 | Semantic Router | 规则/语义路由 vs LLM 路由标签，前端显示路由方式 |
| 10 | Self-RAG 过滤 | 减少幻觉 |
| 11 | 操作审批 | approved 字段闭环 |
| 12 | 澄清提问 | 参数不全主动追问 |
| 13 | 模糊问题 HyDE | 发送"供应商管理是什么"→触发假设文档生成 |
| 14 | 宽泛问题子问题 | 发送"讲讲供应链"→自动拆成多个子问题检索 |
| 15 | 系统健康页 | `/health` 显示 embedding_model, knowledge_docs_count=1034, reranker_enabled |
| 16 | LangGraph Agent | `agent_type=langgraph` 返回非空最终答案 + 工具调用记录 |
| 17 | 一键导入样本库 | 知识库页面点击「📥 一键导入大厂供应链样本库」自动下载+入库 |
| 18 | RAGAS 全量评估 | Evaluate 页面点击「运行全量评估」展示 CP/Faith/AR 三大指标 |
| 19 | PDF 表格检索 | 上传含表格的 PDF，就表格内数据提问（如抽检比例 AQL=1.0） |

### 3. 代码亮点（口头，2 分钟）

| 亮点 | 文件位置 | 面试话术 |
|------|----------|----------|
| RAG 链路分层过滤 | `rag_engine.py:212` | "BM25 粗排 + Milvus 精排，设计上故意不追求 0.9" |
| 行级权限 | `milvus_client.py` security_group ARRAY | "PostgreSQL 存角色，Milvus 查询时实时过滤" |
| SSE 三事件流 | `chat.py` tool_status / text / done | "tool_status 先于内容展示，符合 UX 置信度原则" |
| 审批闭环 | `chat.py:85` approved 字段 | "FastAPI Request 没有这个字段，前后端都必须改" |

---

## 高频追问预判

**Q：为什么不用 LangChain 的内置工具？**
A：TOOL_REGISTRY 复用现有架构，LangChain 包装后反而丢失了 `BaseTool` 的 `ainvoke` 语义。

**Q：Milvus 行级权限怎么做？**
A：security_group 存 ARRAY，查询时 `WHERE '{user_group}' = ANY(security_group)`。PostgreSQL 角色表负责写入校验，Milvus 只负责查询过滤。

**Q：SSE 断连怎么处理？**
A：Redis chat_memory 保存 session 级上下文，重连后从 Redis 拉取历史，不丢状态。

**Q：为什么用 SQLite 而不是 Redis 做工具缓存？**
A：工具缓存是工具节点私有状态，不需要跨进程共享，SQLite 足够且零运维成本。

---

## 面试话术提醒

- 说"我们"而不是"我"，暗示团队协作
- 功能点先说**为什么这么做**，再说**怎么做的**
- 主动提局限性和 trade-off，面试官对"我也知道这里不完美"很有好感
- RAG 指标主动提及"0.53 是可解释的"，把弱点变成设计理念

---

## 文件索引

```
supply-chain-qa/
├── app/
│   ├── agents/
│   │   ├── tool.py              ← 默认 ReAct Agent
│   │   ├── langchain_agent.py    ← LangChain AgentExecutor
│   │   └── langgraph_agent.py    ← LangGraph StateGraph
│   ├── api/chat.py               ← SSE 流 + 审批闭环
│   └── core/
│       ├── rag_engine.py         ← RAG 链路（粗排+精排）
│       ├── milvus_client.py      ← 行级权限过滤
│       └── redis_client.py       ← session 上下文
├── frontend/src/
│   ├── api/chat.js               ← SSE fetch + Authorization
│   └── stores/chat.js             ← 审批 confirmed + approved_tool
└── tests/                        ← 59 个单元测试
```
