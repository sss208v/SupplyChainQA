# Supply Chain QA — 供应链智能问答系统

面向制造业供应链场景的 RAG + Multi-Agent 问答助手。员工用自然语言提问，系统从知识库检索答案或调用后端系统查询实时数据。

## 技术栈

- 前端：Vue3 + Element Plus + Pinia + Vite
- 后端：Python + FastAPI + LangChain
- 向量库：Milvus（BGE-small-zh-v1.5, 512维）
- 缓存：Redis（对话记忆 + Query Cache + Token）
- 数据库：PostgreSQL（RBAC + 反馈）
- LLM：DeepSeek API（可切换 MiniMax/Ollama）

## 架构

```
用户 → Vue3 前端 → FastAPI 后端
                      │
                      ├─ 意图路由（规则 → 语义路由 → LLM 三级）
                      │
                      ├─ RAG Agent
                      │   ├─ Query 复杂度分析（自适应检索深度）
                      │   ├─ 混合检索（向量 + BM25 + RRF 融合）
                      │   ├─ Self-RAG 过滤 + 父子文档扩展
                      │   ├─ 三层置信度路由（直接/改写/Web搜索）
                      │   └─ 引用溯源 [1][2] + Faithfulness 检测
                      │
                      ├─ Tool Agent（ReAct + LangChain 双模式）
                      │   └─ 库存查询 / 订单查询 / 创建工单
                      │
                      └─ 安全层
                          ├─ RBAC（admin/manager/employee）
                          ├─ 文档级可见性隔离
                          ├─ Guardrails（输入白名单 + 输出黑名单）
                          └─ PII 脱敏

              Milvus | Redis | PostgreSQL
```

## 核心功能

### 意图路由（三级）

1. 规则匹配（<1ms）：关键词/正则，零延迟
2. 语义路由（<10ms）：预计算路由 embedding，余弦相似度匹配，零 token
3. LLM 分类（~2.5s）：兜底，处理复杂语义

### RAG 检索

- 混合检索：Milvus 向量 + BM25 关键词，RRF 融合排序
- Query 复杂度分析：简单查询走轻量检索，复杂查询走完整流程
- Self-RAG：LLM 判断 chunk 相关性，过滤低分噪音
- 父子文档：小 chunk 精确匹配 → 大 chunk 提供上下文
- Query Cache：MD5 key，5 分钟 TTL，LRU 淘汰

### 工具调用

- 手写 ReAct 循环（默认）：JSON 格式 Thought/Action/Observation，最多 5 轮
- LangChain AgentExecutor（可切换）：create_react_agent 标准实现
- 工具：query_inventory / query_order / create_ticket / get_datetime / get_knowledge

### 权限管理

- RBAC 三级角色：admin（全部）、manager（上传/删除）、employee（查询）
- 文档级可见性：upload 时指定 visibility=public/admin_only
- 查询时按角色过滤：admin 看全部，employee 只看 public 文档

### 文档解析

- PDF：pymupdf4llm（结构化 Markdown，保留表格/标题）→ opendataloader → pdfplumber 三级回退
- DOCX：python-docx（段落+表格）→ pymupdf 回退
- 支持格式：PDF / DOCX / TXT / MD

### 其他

- SSE 流式输出：Token 级逐字输出 + 多事件类型
- Token 成本追踪：实时显示 token 用量和费用
- Guardrails：输入白名单（50 个供应链关键词）+ 输出黑名单（28 个敏感词）
- DAG 可视化：SVG 绘制 RAG 流水线，7 节点实时状态
- Faithfulness：关键词覆盖率检测

## 快速开始

### 1. 启动基础设施

```bash
docker-compose up -d
```

### 2. 配置后端

```bash
cd backend
# 创建 .env，填入 DEEPSEEK_API_KEY
```

### 3. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### 4. 启动前端

```bash
cd frontend
pnpm install
pnpm dev
# 访问 http://localhost:3000
```

### 5. 上传知识库

访问 http://localhost:3000/knowledge，上传 knowledge/ 目录下的供应链文档。

## 项目结构

```
supply-chain-qa/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 入口
│   │   ├── config.py                  # 配置管理
│   │   ├── agents/
│   │   │   ├── router.py              # 意图路由
│   │   │   ├── rag.py                 # RAG Agent
│   │   │   ├── tool.py                # Tool Agent
│   │   │   └── langchain_agent.py     # LangChain Agent
│   │   ├── api/
│   │   │   ├── chat.py                # SSE 流式对话
│   │   │   ├── knowledge.py           # 知识库管理
│   │   │   ├── auth.py                # RBAC 认证
│   │   │   └── tool.py                # 工具 API
│   │   ├── core/
│   │   │   ├── rag_engine.py          # RRF + Query Cache
│   │   │   ├── llm_router.py          # LLM 工厂
│   │   │   ├── milvus_client.py       # Milvus 客户端
│   │   │   ├── semantic_router.py     # 语义路由
│   │   │   ├── query_analyzer.py      # 复杂度分析
│   │   │   ├── confidence_router.py   # 置信度路由
│   │   │   ├── self_rag.py            # Self-RAG
│   │   │   ├── faithfulness.py        # 幻觉检测
│   │   │   ├── guardrails.py          # 内容安全
│   │   │   ├── clarify.py             # 澄清提问
│   │   │   ├── retry.py               # 重试装饰器
│   │   │   └── auth.py                # RBAC
│   │   └── models/
│   │       └── user.py
│   ├── knowledge/                     # 供应链文档
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   │   ├── Chat/
│   │   │   ├── Knowledge/
│   │   │   ├── Tools/
│   │   │   └── Evaluate/
│   │   ├── stores/
│   │   ├── api/
│   │   └── components/
│   └── vite.config.js
├── docker-compose.yml
└── README.md
```

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/v1/chat/stream | POST | SSE 流式对话 |
| /api/v1/chat/completions | POST | 非流式对话 |
| /api/v1/knowledge/upload | POST | 上传文档 |
| /api/v1/knowledge/list | GET | 文档列表 |
| /api/v1/auth/login | POST | 登录 |
| /api/v1/tools/list | GET | 工具列表 |

## 默认账号

- admin / admin123
