# SmartQA Reranker 启用分析 — OpenSpec 需求规格

> 版本: 1.0 | 作者: Hermes | 日期: 2026-05-12

---

## 1. 现状盘点

### 1.1 代码状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `RerankerEngine` 类 | ✅ 完整 | `init()` + `rerank()` + 降级策略齐备 |
| `RAGEngine.search()` 集成 | ✅ 完整 | 第 518 行根据 `RERANKER_ENABLED` 分流 |
| `main.py` 预热逻辑 | ✅ 已修复 | 131 行检查 `RERANKER_ENABLED` 再加载 |
| `.env` 开关 | ❌ **false** | 当前被关闭 |
| 面试展示页描述 | ❌ 无 | showcase 没提 reranker 的存在和能力 |

### 1.2 当前效果（无 Reranker）

RAGAS 17 题评估结果：
- Context Precision: **0.53** ← 主要原因：无精排时噪声 chunks 排在前面
- Context Recall: **0.47** ← 原因同上
- 检索链路：Vector(embedding) + BM25 → RRF 融合 → **按原始分数排序** → Top-3

### 1.3 Reranker 能做什么

BGE-Reranker-v2-m3 是 cross-encoder 模型，对每个 (query, doc) 对做深度语义匹配打分。与双塔 embedding 不同：
- embedding: query 和 doc 分别编码，余弦相似度（快但粗）
- reranker: query 和 doc 拼接后一起编码（慢但准）

**行业数据：** 启用 Reranker 后 NDCG 典型提升 5-15%（Zilliz/Azure AI Search 实测），Precision@K 提升更显著。

---

## 2. 需求分析

### REQ-1: 在面试 Demo 中启用 Reranker [P0]

**描述:** 将 `RERANKER_ENABLED` 改为 `true`，让面试演示时展示完整检索链路。

**为什么是 P0:** 
- 当前 CP=0.53、CR=0.47 这两个数字在面试中很难解释——面试官会追问"为什么这么低"
- 启用 Reranker 后预计提升到 CP≈0.65-0.70、CR≈0.60-0.65，数字更好看且可解释
- 面试时能展示"完整检索链路"——Vector+BM25+RRF+Reranker 四步，比"前两步+Reranker关"强得多

**当前阻塞:** `RERANKER_ENABLED=false` 的原因已解决（main.py 预热 bug 已修），但仍有风险需要评估。

**验收标准:**
- [ ] `.env` 中 `RERANKER_ENABLED=true`
- [ ] 后端启动成功，日志显示"重排序模型加载完成"
- [ ] 跑 3-5 条查询，确认 rerank_score 字段有非零值
- [ ] 重新跑 RAGAS 5 题快速评估，对比启用前后的分数变化

**面试价值:** 
- 能说：「我们用了 BGE-Reranker-v2-m3 做精排，把 Context Precision 从 0.53 提升到 X」
- 展示完整检索链路：Embedding → BM25 → RRF 融合 → Reranker 精排

---

### REQ-2: 评估 Reranker 的性能影响 [P1]

**描述:** 测量启用 Reranker 后的延迟增加和效果提升的 trade-off。

**关键数据需要收集:**
- [ ] 单次查询平均延迟（无 Reranker）：已知约 50ms 检索
- [ ] 单次查询平均延迟（有 Reranker）：预计增加 1-8 秒（CPU cross-encoder）
- [ ] 首次查询延迟（含模型预热）：预计增加 3-15 秒
- [ ] RAGAS CP/CR 对比：无 vs 有 Reranker

**面试价值:** 能说出 trade-off 具体数字——「Reranker 让检索精度提升 X%，代价是每次查询多 Y 秒。面试官面 RAG 必问这个。」

---

### REQ-3: 面试话术准备 [P1]

**描述:** 更新 interview-showcase.html，加入 Reranker 的 trade-off 分析和面试话术。

**验收标准:**
- [ ] 在检索链路部分补充 Reranker 的作用和数据
- [ ] 新增 FAQ：「Reranker 为什么慢？值不值得？」+ 话术
- [ ] 指标速查表更新（如果 REQ-2 跑出数字）

---

## 3. 风险评估

### 3.1 已知风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| HF Hub 限流导致模型下载/验证超时 | 中 | 启动失败 | 已修复 warmup bug，失败只打 warning 不阻塞 |
| 首次查询延迟 8s+（CPU cross-encoder） | 高 | 面试演示第一问体验差 | 预热阶段加载模型（已实现），首问延迟降到 3-5s |
| 后续查询延迟 1-3s | 高 | 可接受 | 面试话术：「CPU rerank 有代价，生产用 GPU 可降到 50ms」 |
| 模型权重 1.1GB 未本地缓存 | 低 | 需要下载 | 之前测试时已下载到 HuggingFace 缓存 |

### 3.2 决策矩阵

| 方案 | 检索精度 | 查询延迟 | 面试表现 | 风险 |
|------|----------|----------|----------|------|
| **A: 不启用** (当前) | CP=0.53, CR=0.47 | ~50ms | 数字难看，需大量解释 | 无 |
| **B: 启用** | CP≈0.65-0.70 | +1-8s | 链路完整，有 trade-off 可讲 | 启动可能慢 |
| **C: 条件启用** (仅面试时) | 同 B | 同 B | 同 B | 同 B |

**推荐方案 B**——因为阻塞原因已消除，且面试时需要展示完整能力。延迟增加是缺点但也是面试素材：「我清楚地知道 trade-off」。

### 3.3 如果不启用

如果不启用 Reranker，面试时需要准备好以下解释：
- 「当前 CP/CR 偏低的原因是 Reranker 关闭了——这是一个有意识的选择」
- 「在 demo 阶段先用 RRF 融合保证延迟，Reranker 的代码和集成已经 complete」
- 「生产环境中启用 Reranker 后 CP 预计提升到 0.65+」

这个话术也能用，但不如直接启用了说「我们实测 CP 从 0.53 提升到了 0.68」有力。

---

## 4. 非需求

- ❌ GPU 加速 Reranker（当前没有 GPU 环境给 Python）
- ❌ 多模型 Reranker 对比（bge-reranker-large vs v2-m3 等）
- ❌ 延迟精确到 ms 的 benchmark（口头说数量级即可）
- ❌ Reranker 缓存（后续优化，先展示基础能力）

---

## 5. 实施任务（待 Phase 2）

| # | 任务 | 估时 | 涉及文件 |
|---|------|------|----------|
| 1 | 改 `.env`：`RERANKER_ENABLED=true` | 1min | `.env` |
| 2 | 重启后端，确认日志显示"重排序模型加载完成" | 5min | `main.py` |
| 3 | 跑 3-5 条查询验证 rerank_score 非零 | 5min | 测试脚本 |
| 4 | 重跑 RAGAS 5 题快速评估，对比 CP/CR | 3min | `eval/run_ragas_quick_ds.py` |
| 5 | 更新 interview-showcase 指标 + 话术 | 10min | `docs/interview-showcase.html` |
```

## 6. 预期效果对比（估算）

| 指标 | 无 Reranker (当前) | 有 Reranker (预估) | 提升 |
|------|-------------------|-------------------|------|
| Context Precision | 0.53 | **0.65-0.70** | +22-32% |
| Context Recall | 0.47 | **0.55-0.62** | +17-32% |
| Faithfulness | 0.77 | 0.75-0.78 | 基本不变 |
| Answer Relevancy | 0.68 | 0.67-0.70 | 基本不变 |
| 单次检索延迟 | ~50ms | **+500-2000ms** | — |
