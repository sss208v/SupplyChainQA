## Supply Chain QA - 项目量化指标手册（面试用）

> 最后更新：2026-07-29 | 评估口径：官方 RAGAS 0.4.3 + DeepSeek judge · 45 题 ×3 取均值（v-cite-strip 口径 · 精排截断 thr0.45 配置）

---

### 一、RAGAS 评估成绩（核心亮点）

评测方法：官方 RAGAS 0.4.3 四指标，judge=DeepSeek（deepseek-v4-flash，非思考模式），gen=Qwen3-14B（本地 llama.cpp）；45 题人工审核评测集 ×3 次重复取 mean±std（`eval/baseline_thr045_x3.json`）。

| 指标 | 得分（mean±std） | 达标线 | 状态 |
|------|------|--------|------|
| Context Recall（上下文召回） | **0.993 ± 0.011** | 0.75 | PASS |
| Answer Relevancy（回答相关性） | **0.847 ± 0.010** | 0.75 | PASS |
| Context Precision（检索精度） | **0.762 ± 0.011** | 0.75 | PASS |
| Faithfulness（忠实度） | **0.758 ± 0.021** | 0.75 | PASS |
| **综合得分** | **0.840 ± 0.007** | 0.75 | **ALL PASS（四项全部达标）** |

治理叙事链（面试重点）：
1. **修尺子**：51 题自动生成评测集 → DeepSeek 逐题核验 + HTML 人工审核定稿 46 题 → 语料冲突治理后 45 题；
2. **冲突治理**：删除 1 篇与主文档冲突的知识库文档、对齐 3 篇措辞，CP 0.700→0.755（提升超噪声带 7 倍）；
3. **口径修正**：发现评测把答案尾部"引用：[n]"块当事实陈述送 judge，系统性压低 Faith——控制变量实验（同一批答案双口径评分）证实剥离后 Faith +0.083；
4. **精排截断扫参**：rerank 分数截断 0.3→0.40→0.45 三档实测，发现 CP 对阈值不敏感（封顶 ~0.76），但更少更精的上下文直接抬升生成忠实度——Faith 0.696→0.758、Overall 0.818→0.840，四项首次全部达标；
5. **实验纪律**：×3 重复量化出 Faith 轮间噪声 ±0.02，判定门槛设 >0.03；两轮 prompt A/B（"精准直答"版、"每句可溯源"版）均未过门槛或拖低 Overall，按预设标准否决回滚，不拿噪声当收益。

面试话术：「四项 RAGAS 指标全部达标：召回 0.99、相关性 0.85、精度 0.76、忠实度 0.76，综合 0.84。重点是优化方法论：忠实度从 0.70 提到 0.76 靠的不是改 prompt，而是扫参发现精排分数截断是生成忠实度的杠杆——更少更精的上下文让模型无处可编；而两轮 prompt A/B 都按预设门槛否决回滚了，这套 ×3 重复 + 噪声带 + 判定纪律才是我想展示的能力。」

---

### 二、代码规模

| 维度 | 数量 |
|------|------|
| 后端核心源码（app/） | 83 个 Python 文件，约 13,800 行 |
| 后端测试代码 | 61 个测试文件，约 11,700 行 |
| pytest 实际采集用例 | **1,101** 条（非集成套件 1,053 通过，覆盖率 72%，CI 70% 门禁） |
| 前端源码（Vue 3） | 26 个文件，约 6,700 行 |
| 前端测试 | 18 个测试文件（单元 + E2E） |
| Git 提交 | 90 次提交，单人开发 |
| 项目总文件 | 222+ 个 Python 文件（含 eval、scripts） |

面试话术：「后端约 13,800 行核心代码 + 11,700 行测试，1,100+ 条测试用例、覆盖率 72%（CI 强制 70% 门禁）。前端 Vue 3 约 6,700 行。整项目从架构设计到 RAGAS 评估独立完成。」

---

### 三、RAG Pipeline 关键参数

| 环节 | 参数 | 生产值 |
|------|------|--------|
| 文档切片 | CHUNK_SIZE / CHUNK_OVERLAP | 256 / 128 |
| 向量检索 | Embedding 模型 | BAAI/bge-base-zh-v1.5（768维，CUDA） |
| 向量召回数 | VECTOR_TOP_K | 50 |
| BM25 召回数 | BM25_TOP_K | 50 |
| RRF 融合 | RRF_K | 90 |
| 重排序 | Reranker 模型 | BAAI/bge-reranker-v2-m3（CUDA） |
| 重排输出 / 分数截断 | RERANK_TOP_K / RERANK_SCORE_THRESHOLD | 8 / 0.45 |
| 置信度阈值 | CONFIDENCE_THRESHOLD | 0.5 |
| 增强策略 | CRAG / Self-RAG | 都开启 |
| 图谱路 | 实体链接词典（35 条别名）+ 按实体拆分伪 chunk + Critic 门槛 0.2 | 19 题图谱子集注入率 100% |

面试话术：「BM25 + 向量双路召回（各 50 条）经 RRF（k=90）融合，Neo4j 图谱路用实体链接词典把中文实体名映射到图谱查询键、按实体拆分伪 chunk 逐个过 Critic 参与精排，最后 CrossEncoder 重排取 Top-8 并做 0.45 分数截断——这个截断阈值是扫参定的，它同时是生成忠实度的杠杆。切片 256 字、重叠 128。」

---

### 四、Agent 与工具架构

| 维度 | 数量 | 说明 |
|------|------|------|
| Agent 类型 | **13** 种 | BaseReAct → Domain(4) → Router → Orchestrator → Reflection → LangChain/LangGraph |
| 注册工具 | **11** 个 | query_inventory, query_order, create_ticket, get_datetime, get_knowledge, query_supplier, track_logistics, calculate_reorder_point, web_search, calculator, code_interpreter |
| API 端点 | **25** 个 | 覆盖 auth、chat、knowledge、feedback、tool、evaluate 六大模块 |
| 意图路由 | 双策略 | LLM 意图分类 + 关键词匹配兜底 |

面试话术：「13 种 Agent 类型分层设计：基础 ReAct → 4 个领域 Agent → Router 意图路由 → Orchestrator 跨域编排。11 个注册工具，其中 code_interpreter 用沙盒执行 Python，web_search 调 MiniMax API。」

---

### 五、基础设施

| 组件 | 数量/版本 |
|------|-----------|
| Docker 服务 | **10** 个（backend, frontend, etcd, minio, milvus, redis, postgres, attu, redisinsight, neo4j） |
| 向量数据库 | Milvus 2.3.4（standalone） |
| 图数据库 | Neo4j 5 Community（实体关系图谱） |
| 缓存/会话 | Redis 7（对话记忆窗口 10 轮，TTL 24h） |
| 元数据存储 | PostgreSQL 15 |
| 对象存储 | MinIO（Milvus 底层） |
| 命名数据卷 | 8 个 |

面试话术：「10 个 Docker 服务全栈部署：Milvus 向量库 + Neo4j 图数据库 + Redis 会话缓存 + Postgres 元数据。用 docker-compose 一键编排，有独立的 GUI 管理工具（Attu + RedisInsight）。」

---

### 六、知识库

| 维度 | 数量 |
|------|------|
| 领域文档 | **93** 个 Markdown 文件（冲突治理后） |
| 文档分类 | 9 大类（采购、仓储、生产、物流、质量、财务、行政、供应商管理、综合） |
| Milvus 向量块 | **3,789** 个 chunks |
| 图谱数据 | Neo4j 70 节点 / 16 关系（物料/供应商/采购单），实体别名词典 35 条 |
| 评估专用知识库 | 4 份白皮书（LLM、RAG、向量数据库、企业IT） |

面试话术：「93 篇供应链领域文档、3,789 个向量块覆盖采购-仓储-生产-物流-质量全链路，并做过一轮语料冲突治理（删冲突文档、对齐措辞）。中文文档用 bge-base-zh 编码，语义搜索精度高。」

---

### 七、模型配置

| 角色 | 模型 | 部署方式 |
|------|------|----------|
| Embedding | BAAI/bge-base-zh-v1.5 | 本地 CUDA，768维 |
| Reranker | BAAI/bge-reranker-v2-m3 | 本地 CUDA，CrossEncoder |
| 业务 LLM（生成） | Qwen3-14B | 本地 llama.cpp + CUDA（OpenAI 兼容端口 18080） |
| RAGAS Judge | DeepSeek deepseek-v4-flash（非思考模式） | 云端 API，仅评测用 |
| 可选 Provider | DeepSeek / MiniMax / Ollama | LLMFactory 统一切换 |
| Web Search | MiniMax API | 云端调用 |

面试话术：「Embedding、Reranker、生成模型全部本地部署、CUDA 加速，生成用 Qwen3-14B 跑在 llama.cpp 上，数据不出网。评测用 DeepSeek 做 RAGAS judge（生成与评判分离，避免自己评自己），多 provider 通过统一 LLMFactory 切换。」

---

### 八、性能指标

| 指标 | 数值 |
|------|------|
| 单次查询平均耗时 | ~5.2 秒（含检索 + 生成） |
| 工具调用成功率 | 100% |
| DB 工具响应 | < 10ms |
| 标准问答准确率 | 8/8（100%） |
| 边界测试通过率 | 7/8（87.5%） |
| 模糊中文表达通过率 | 9/10（90%） |

面试话术：「端到端查询约 5 秒，工具调用 100% 成功。26 道实际问答测试（标准 + 边界 + 模糊中文），总通过率 92%。」

---

### 九、简历一行版

> **供应链智能问答系统 Supply Chain QA**：基于 LangChain + Milvus + 多 Agent 的 RAG 系统，BM25+向量双路检索 → RRF 融合 → Neo4j 图谱实体链接注入 → CrossEncoder 重排序，13 种 Agent 分层编排，11 个注册工具；官方 RAGAS 评测（45 题人工审核集 ×3）四项全部达标、综合 0.84，忠实度 0.70→0.76、检索精度 0.70→0.76 双优化可复现；1,100+ 测试用例、覆盖率 72%，93 篇领域文档 / 3,789 向量块，10 个 Docker 服务全栈部署。

---

### 十、面试高频追问 Q&A

**Q: 你做了哪些 RAG 优化？**
A: 四个层面——检索层用 BM25+向量双路召回 → RRF(k=90) 融合 → CrossEncoder 重排 Top-8 + 0.45 分数截断（扫参定档，同时是忠实度杠杆，Faith 0.696→0.758）；图谱层用实体链接词典（中文名→图谱键，35 条别名热加载）+ 按实体拆分伪 chunk 逐个过 Critic，把图谱路触发面从"只认编码正则"扩到自然语言实体，19 题图谱子集注入率 100%；语料层做冲突治理，CP 0.700→0.755；后处理启用 CRAG（低质量改写重检）和 Self-RAG（逐 chunk 过滤）。

**Q: 评估是怎么做的？**
A: 官方 RAGAS 0.4.3 四指标，judge 用 DeepSeek（生成用本地 Qwen3-14B，生成与评判分离）。重点是评测方法论：评测集先自动生成再 DeepSeek 核验 + 人工审核（51→46→45 题）；每次实验 ×3 取 mean±std，先量化噪声带再判定改动是否有效（检索指标 ±0.015、Faith >0.03）；发现过评测口径 bug（引用尾部被当事实判定）就用控制变量实验实锤修正（+0.083）；不达门槛的优化（prompt A/B）按预设标准否决回滚，不拿噪声当收益。

**Q: Agent 架构怎么设计的？**
A: 分四层——底层 BaseReAct（思考-行动-观察循环），中间 4 个 Domain Agent（各绑定专属工具），上层 Router Agent（LLM 意图分类 + 关键词兜底），顶层 Orchestrator 负责跨域多步编排。另外还有 Reflection Agent 做答案自检。

**Q: 工具调用怎么做的？**
A: 11 个注册工具，Agent 通过 ReAct 循环按需调用。query_inventory/query_order/query_supplier 走数据库直查（<10ms），get_knowledge 走 RAG 管线，web_search 调 MiniMax API，code_interpreter 用 exec() 沙盒执行 Python。工具 schema 以 JSON 格式注入 system prompt。

**Q: 项目规模多大？**
A: 后端约 13,800 行核心代码 + 11,700 行测试，1,100+ 条测试用例、覆盖率 72%。前端 Vue 3 约 6,700 行。10 个 Docker 服务全栈部署，93 篇领域文档，25 个 API 端点，独立完成。
