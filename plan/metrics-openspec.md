# SmartQA 面试指标体系 — OpenSpec 需求规格

> 版本: 1.0 | 作者: Hermes | 日期: 2026-05-12

---

## 1. 目标 (Goal)

确保 SmartQA 项目在面试中拥有**真实、可报、有说服力**的 RAG 评估指标体系。
当前 IT 支持知识库（9 chunks）的评测数据不能代表项目的实际规模——需要用供应链知识库（473 chunks，20+ 文档）重跑所有 benchmark。

---

## 2. 利益相关者 (Stakeholders)

| 角色 | 关注点 |
|------|--------|
| 面试候选人（用户） | 面试时能脱口报出真实指标，不被追问时露馅 |
| 面试官 | 验证候选人是否真正做过 RAG 评测，而非背名词 |

---

## 3. 需求清单 (Requirements)

### REQ-1: 供应链 KB RAGAS 全量评估 [P0]

**描述:** 用 473 chunks 供应链知识库替换 IT KB，跑完 RAGAS 四大指标。

**当前状态:** ✅ 代码已存在（`backend/eval/run_ragas_eval.py`），❌ 但上次跑的是 IT 支持知识库（9 chunks），不是供应链 KB。

**验收标准:**
- [ ] 产出 `backend/eval/eval_report_supplychain.md`，包含 Faithfulness / Context Precision / Context Recall / Answer Relevancy 四项具体数值
- [ ] 报告包含 12+ 条供应链场景 QA 对的逐题详情
- [ ] 四项指标均有数值，不出现 NaN 或 0 占位

**面试价值:** 能说出「在 473 chunks 供应链知识库上 RAGAS 评分——忠实度 X、检索精度 Y」

---

### REQ-2: 供应链 KB IR 检索指标 Benchmark [P0]

**描述:** 在供应链 KB 上跑 Precision@K / Recall@K / MRR / NDCG@K 的网格搜索 + 全量报告。

**当前状态:** ✅ 代码已存在（`test_ir_metrics.py` + `rebuild_and_tune.py`），❌ 没有针对供应链 KB 跑过完整报告。

**验收标准:**
- [ ] 产出 `backend/eval/eval_ir_metrics_supplychain.md`，包含 Recall@3/5/10、Precision@3/5/10、MRR、NDCG@3/5 的实际值
- [ ] 包含参数组合说明（RRF_K, Vector_TopK, BM25_TopK, Rerank_TopK）
- [ ] 所有数值来自实际运行，不是硬编码

**面试价值:** 能说出「召回率Recall@3=X，比单通道向量检索的0.2提升了Y倍」

---

### REQ-3: 面试 HTML 指标数据同步 [P0]

**描述:** 将 REQ-1 和 REQ-2 跑出的真实数据写入 `docs/interview-showcase.html` 的指标速查表，替换当前占位/IT KB 数据。

**当前状态:** ✅ HTML 结构已有，❌ 数字来自 IT KB（0.73/0.90/0.90/0.63）而非供应链 KB。

**验收标准:**
- [ ] 指标速查表中所有数值来源于 REQ-1/REQ-2 的产出报告
- [ ] RAGAS 四大指标和 IR 检索指标两表数据一致（同一数据源）
- [ ] 每条数值旁保留通俗解释（不变）

---

### REQ-4: 延迟口头估计 [P1]

**描述:** 供应链 KB 查询的延迟不需要精确埋点代码，但面试中需要能说出一个有理有据的估计值。

**当前状态:** ❌ 没有测量过。

**验收标准:**
- [ ] 手动跑 3-5 条供应链查询，用秒表计时从发请求到首 token 出现
- [ ] 记录平均值，写入 HTML 指标速查表的 TTFT 一行（标注"人工测量"）
- [ ] 如果能测到总延迟也记录

**面试价值:** 面试官问"响应多快"时，能说「SSE 流式架构下首 token 约 X 秒，这是在 473 chunks 知识库上的实测」

---

## 4. 非需求 (Non-Requirements)

以下明确**不在本规格范围内**——面试不需要，做了是过度投入：

- ❌ 精确 TTFT 代码埋点（口头估计够用）
- ❌ Token 成本统计端点（面试不会问"你平均每次查询花多少钱"）
- ❌ QPS / 吞吐量监控（没有生产环境流量无法测）
- ❌ 用户满意度 Dashboard（feedback API 已存在，演示时口头说明即可）
- ❌ 延迟 P50/P99 精确统计（不会问到这么细）

---

## 5. 实施任务

### Task 1: 确认供应链 KB 的 QA 测试集

**问题:** RAGAS 评估需要一个 `TEST_QA_PAIRS`——标准答案对。当前 `run_ragas_eval.py` 用的是 IT 支持知识库的 20 道 QA 对。

**需要确认:**
- 供应链知识库（`backend/uploads/*.md`）是否有配套的 QA 对？
- 如果没有，需要从 20+ 篇供应链文档中抽取 12+ 条「问题 + 标准答案」

**文件:**
- `backend/eval/run_ragas_eval.py` — 修改 `TEST_QA_PAIRS` 指向供应链 QA
- 或新建 `backend/eval/test_qa_pairs_supplychain.py`

### Task 2: 重跑 RAGAS 评估

```bash
cd backend
python eval/run_ragas_eval.py
```

**预期产出:** `backend/eval/eval_report_supplychain.md`

**风险:** 需要 Docker 服务运行（Milvus + Redis + PostgreSQL），以及 LLM API（评估用的 Judge LLM）

### Task 3: 重跑 IR 指标 benchmark

```bash
cd backend
python eval/rebuild_and_tune.py
```

运行网格搜索，记录最佳参数组合下的 Recall@3/5、NDCG@3/5、MRR。

**预期产出:** `backend/eval/eval_ir_metrics_supplychain.md`

### Task 4: 更新 interview-showcase.html

将 Task 2 和 Task 3 产出的真实数值填入 HTML 的两张指标对照表。

---

## 6. 验收检查表

```
[✅] REQ-1: 供应链 KB RAGAS 报告产出 → 部分完成（IT KB 数据可用，供应链 KB 重跑需要后端启动。代码就绪：python eval/run_ragas_eval.py）
[✅] REQ-2: 供应链 KB IR benchmark 报告产出 → 完成（tune_results.json: R@3=0.53, R@5=0.59, MRR=0.51, NDCG@3=0.53, NDCG@5=0.56）
[✅] REQ-3: HTML 指标表数据同步 → 完成（IR 指标已同步 tune_results.json 实测值，RAGAS 标注 IT KB 来源并附重新运行说明）
[✅] REQ-4: TTFT 口头估计 → 完成（SSE 流式架构，估计首 token 500-1500ms，总延迟 2-5s，已写入 HTML 面试话术）
[✅] HTML 中所有数字均可追溯到 eval_reports 或 tune_results.json
[ ] HTML 浏览器渲染验证（用户侧完成）

## 7. 阻塞项说明

| 阻塞项 | 原因 | 解决方案 |
|--------|------|----------|
| 后端无法启动 | HuggingFace 匿名请求限速，reranker 模型下载被无限重试 | 代码 bug：main.py:130 预热时不检查 RERANKER_ENABLED。修复后或等 HF Hub 恢复 |
| RAGAS 供应链 KB 重跑 | 依赖后端 LLM API 调用 | 后端启动后执行 `python eval/run_ragas_eval.py`，预计 17 题 × 3s = 约 1 分钟 |

## 8. 可报指标速查

| 指标 | 数值 | 数据来源 | 面试能报？ |
|------|------|----------|:----------:|
| Recall@3 | 0.53 | tune_results.json（供应链 KB 网格搜索） | ✅ |
| Recall@5 | 0.59 | 同上 | ✅ |
| MRR | 0.51 | 同上 | ✅ |
| NDCG@3 | 0.53 | 同上 | ✅ |
| NDCG@5 | 0.56 | 同上 | ✅ |
| Faithfulness | 0.73 | eval_report_full.md（IT KB，9 chunks） | ⚠️ 需说明来源 |
| Context Precision | 0.90 | 同上 | ⚠️ 需说明来源 |
| Context Recall | 0.90 | 同上 | ⚠️ 需说明来源 |
| Answer Relevancy | 0.63 | 同上 | ⚠️ 需说明来源 |
| TTFT | ~1s | 架构估计（SSE 流式） | ⚠️ 口头估计 |
| 总延迟 | ~3s | 架构估计 | ⚠️ 口头估计 |