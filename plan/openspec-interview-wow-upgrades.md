# SmartQA Pro — 面试专属“降维打击”高级功能升级 OpenSpec

> **版本**: 3.0 | **日期**: 2026-05-20 | **定位**: 供应链智能问答系统 (SmartQA Pro)
> **目标**: 针对“架构深度、高并发一致性、生产级可观测性、演示级视觉Wow效果”四大硬核面试高频追问方向进行极限完善，提升项目在面试官面前的技术含金量与防杠说服力。
> **升级执行人**: Hermes Agent

---

## 1. 为什么升级（面试痛点驱动）

虽然 SmartQA Pro 目前已经具备了混合检索、自适应 RRF、多 Agent 编排等极佳特性，但是在冲击中高级 AI/架构师岗位时，面试官往往会针对**生产级痛点**进行深入追问。本次升级聚焦于解决以下四个高频痛点：

1. **“空挂图数据库”痛点**：项目集成了 Neo4j，但目前主要用于实体冲突检测，RAG 检索阶段未实现真正的图增强检索（Graph RAG）。
2. **“黑盒决策”痛点**：多 Agent 编排与多级路由的 Trace 调试是黑盒，无法给面试官直观展示每一步的 Token 消耗、提示词和耗时。
3. **“高并发脏数据”痛点**：审批与写操作（如 `create_ticket`）在高并发或网络重试下，缺乏幂等与分布式锁控制，不符合供应链严苛的一致性要求。
4. **“演示无感”痛点**：RAG 评测结果停留在后台的 Markdown 报告或 CSV 中，缺乏极具视觉冲击力的 RAG 链路诊断与指标可视化大盘。

---

## 2. 需求清单与实现规格

### REQ-1：Graph RAG 深度双路检索融合 [P0]
*   **面试价值**：证明你不仅懂 Vector RAG，还懂 Graph RAG 融合。解决语义检索在“多跳实体关联”（如：查特定物料的供应商订单状态）时的长尾召回问题。
*   **实现规格**：
    1.  **实体抽取与子图召回**：在 `app/core/rag_engine.py` 的 `search` 流程中，利用 `app/core/query_analyzer.py` 或轻量级正则，提取 Query 中的实体（如物料 `MAT-001`、订单 `PO-20250101`、供应商 `SUP-001`）。
    2.  **Neo4j 2-Hop 关联检索**：如果检测到实体且 Neo4j 连接可用，执行 Cypher 查询召回该实体 2 步以内的关联三元组（例如：`(Material)-[:SUPPLIED_BY]->(Supplier)-[:HAS_ORDER]->(Order)`）。
    3.  **图上下文注入（Graph Context Injection）**：将召回的图谱三元组关系格式化为“声明式文本段落”（例如：“物料 MAT-001 由供应商 SUP-001 供应，关联采购订单 PO-001 当前状态为待交付”），作为一个特殊的 `retrieval_source: "neo4j_graph"` 的 Chunk。
    4.  **混合重排融合**：将该 Chunk 注入 `_merge_results` 队列中，通过 Reranker 进行二次精排，赋予其合理的 `rerank_score`，使 LLM 能够天然感知图数据库中的关联实体信息。
*   **修改文件**：
    *   `backend/app/core/rag_engine.py` (修改：在 `search` 内部引入 `graph_client` 检索并拼接 Chunk)
    *   `backend/app/core/neo4j_client.py` (修改：新增 `query_2hop_subgraph(entity: str) -> list[str]` 辅助函数)

---

### REQ-2：全局生产级可观测性（一键集成 Langfuse / Langsmith） [P0]
*   **面试价值**：证明你有生产级大模型应用的监控与治理经验。面试官最爱问：“Agent 跑了一堆步骤，哪一步最慢？怎么收集坏例和用户真实报错？”
*   **实现规格**：
    1.  **开箱即用集成**：在 `backend/app/config.py` 中引入 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST` 配置项。
    2.  **LangChain/LangGraph 官方 Callback 无缝绑定**：在 `app/api/chat.py` 和各域 Agent 运行入口，通过 `config={"callbacks": [langfuse_handler]}` 或 `langfuse.decorator` 一键包裹执行链路。
    3.  **Trace ID 全链路透传**：后端在流式 SSE 输出的首个事件中透传 `trace_id`；前端在控制台打印 Trace 链接，并在“Evaluate”页提供“前往 Langfuse 调试此 Trace”的一键跳转按钮。
*   **修改文件**：
    *   `backend/app/config.py` (增加 Langfuse 环境变量支持)
    *   `backend/app/core/observability.py` (新增：初始化 Langfuse 客户端及 Callback Handler)
    *   `backend/app/api/chat.py` (修改：对话主循环集成 Callback，返回 trace_id)
    *   `frontend/src/views/Chat/index.vue` (修改：捕获后端返回的 trace_id 并打印)

---

### REQ-3：基于 Redis 的敏感写操作幂等与分布式锁 [P1]
*   **面试价值**：向面试官证明你具有传统后端的高并发一致性思维，能够将分布式系统（Redis lock）与 AI Agent（写操作工具）完美结合，防止 Agent 脑裂或前端重复审批造成脏数据。
*   **实现规格**：
    1.  **幂等键生成 (Idempotency Key)**：在前端发起审批时，生成唯一的 `request_id` 或基于当前会话及工具参数生成 MD5。
    2.  **Redis 分布式悲观锁 (Redlock 简化版)**：在 `backend/app/core/redis_client.py` 中实现 `redis_lock` 上下文管理器或装饰器。
    3.  **敏感工具防并发重放**：在 `create_ticket`（创建工单）工具执行前，首先使用锁尝试抢占，锁的 Key 为 `lock:tool:create_ticket:{session_id}:{idempotency_key}`。若抢占失败或检测到该 Key 已被成功执行（使用 Redis 记录执行结果，保留 5 分钟），则直接拦截并返回“该操作已在处理中或已执行，请勿重复操作”。
*   **修改文件**：
    *   `backend/app/core/redis_client.py` (修改：实现 `acquire_lock` 和 `release_lock` 异步方法)
    *   `backend/app/agents/domain_agent.py` 或 `backend/app/core/tool_engine.py` (修改：在写操作工具执行处包裹分布式锁与幂等校验)

---

### REQ-4：基于 ECharts 的 RAG 检索诊断流与评测雷达图 [P0]
*   **面试价值**：极具视觉“WOW”效果。在面试演示现场，让大模型检索从“黑盒”变成“白盒可视化”，直观对比向量检索、BM25 和重排前后的名次变化，给面试官强烈的技术冲击。
*   **实现规格**：
    1.  **RAG 检索诊断接口**：后端 `/api/v1/chat` 接口的流式输出中，除返回最终结果外，在 `metadata` 中返回两路检索（Milvus 和 BM25）的前 10 个原始 Chunk 标题、原始得分、RRF 融合分及 Reranker 精排分数。
    2.  **检索桑基图 / 柱状图对比 (Sankey / Bar Chart)**：前端在“Evaluate（评测）”页面新增一个“RAG 检索诊断白盒”选项卡。输入一个测试 Query，展示两路召回的 Chunks 如何通过 RRF 汇聚并被 Reranker 过滤的动态流向或分数对比。
    3.  **Ragas 指标雷达图**：读取后端 `backend/eval/eval_ragas_result_full_sc.json` 的评测结果，在前端通过 ECharts 渲染成精美的雷达图，直观呈现“Faithfulness, Answer Relevance, Context Precision, Context Recall”等专业指标，证明系统的严谨性。
*   **修改文件**：
    *   `frontend/src/views/Evaluate/index.vue` (修改：引入 ECharts，绘制雷达图与检索白盒对比图)
    *   `backend/app/api/chat.py` (修改：增强 metadata 结构，返回详尽的各路检索分数列表)

---

## 3. 任务分配与升级步骤

Hermes Agent 可按照以下三个阶段逐步完成本规格说明书的开发：

### Phase 1：图谱与安全 (REQ-1 & REQ-3) — 3小时
- [ ] 在 `neo4j_client.py` 中新增 `get_2hop_subgraph_context(entity: str) -> str` 函数，生成结构化声明文本。
- [ ] 修改 `rag_engine.py`，实现 Query 实体匹配，双路并行查 Milvus + Neo4j，将图上下文组合成 `retrieval_source="neo4j_graph"` 的伪 Chunk。
- [ ] 确保图谱 Chunk 参与 RRF 与 Reranker 排序流程。
- [ ] 在 `redis_client.py` 中实现分布式锁装饰器 `aioredis_lock(key_prefix: str, expire: int = 10)`。
- [ ] 为 `create_ticket` 等敏感写操作工具绑定锁和幂等校验，防止并发写入。

### Phase 2：可观测性 (REQ-2) — 2小时
- [ ] 增加 `app/core/observability.py`，配置 Langfuse 客户端。
- [ ] 将 Langfuse Callback 注入 FastAPI 中间件与 LangGraph / LangChain 的运行参数中。
- [ ] 在 SSE `chat` 接口流式输出开始时，将 `trace_id` 及 Langfuse 调试 URL 作为元数据推送到前端。
- [ ] 前端捕获并在控制台高亮输出 Trace 调试地址。

### Phase 3：前端 WOW 评测大盘 (REQ-4) — 3小时
- [ ] 在 `/api/v1/chat` 或新增 `/api/v1/eval/debug-retrieval` 接口中返回各路检索分数。
- [ ] 在前端 `Evaluate/index.vue` 中集成 `echarts`。
- [ ] 绘制 **RAG 评估雷达图**：绑定 Ragas 评测 JSON 数据展示。
- [ ] 绘制 **白盒检索对比柱状/桑基图**：直观展示 Chunks 的“向量分 vs BM25分 -> 融合分 -> 精排分”过滤链条。

---

## 4. 验证与回归测试

1.  **单元测试验证**：
    *   运行 `pytest tests -k "not integration"` 确保 81 个单元测试无任何 Regression。
2.  **功能验证**：
    *   人工测试 Graph RAG：提问“物料 MAT-001 相关的订单状态”，查看 SSE 输出中是否包含 `neo4j_graph` 来源标记，核对图谱上下文是否合理注入。
    *   高并发测试：使用 ApacheBench 或轻量级并发脚本连续触发 5 次 `create_ticket` 审批确认，验证仅有 1 次成功，其余 4 次被 Redis 锁或幂等机制拦截。
    *   ECharts 渲染：进入前端“评测大盘”，验证雷达图与 RAG 诊断流能流畅加载并有微交互动画。
