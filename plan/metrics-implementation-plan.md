# SmartQA 面试指标体系 — 实施规划

> **For Hermes:** 逐任务实施，每个任务完成后验证再继续。

**目标：** 补齐面试中需要的四大类指标（检索/生成/系统工程/业务），所有数据真实可追踪。

**架构：** 三条并行线 —
A. 已有代码重跑（用供应链 KB 重新 benchmark）
B. 新增埋点（TTFT、延迟拆解、Token 汇总端点）
C. 整合展示（在 interview-showcase.html 反映真实数据 + 新增监控 Dashboard）

---

## 现状盘点

| 指标 | 四大类 | 代码存在? | 有真实数据? | 需做什么 |
|------|--------|-----------|------------|----------|
| Recall@K | 检索 | ✅ test_ir_metrics.py | ⚠️ 只有 IT KB 数据 | 重跑供应链 KB |
| Precision@K | 检索 | ✅ | ❌ 没跑过 | 重跑 |
| MRR | 检索 | ✅ | ⚠️ 只有 IT KB | 重跑 |
| NDCG@K | 检索 | ✅ | ⚠️ 只有 IT KB | 重跑 |
| Context Precision | 检索(RAGAS) | ✅ | ⚠️ 只有 IT KB | 重跑供应链 KB |
| Context Recall | 检索(RAGAS) | ✅ | ⚠️ 只有 IT KB | 重跑 |
| Faithfulness | 生成 | ✅ | ⚠️ 只有 IT KB | 重跑 |
| Answer Relevancy | 生成 | ✅ | ⚠️ 只有 IT KB | 重跑 |
| TTFT | 系统工程 | ❌ | ❌ | **新增埋点** |
| 总延迟 | 系统工程 | ❌ | ❌ | **新增埋点** |
| Token 成本 | 系统工程 | ⚠️ 前端有 | ❌ 无汇总 | **新增统计端点** |
| QPS/吞吐量 | 系统工程 | ❌ | ❌ | 新增中间件计数 |
| 用户满意度 | 业务 | ✅ feedback API | ❌ 无真实数据 | 已有 API，需填数据 |

---

## Task A: 重跑 Benchmark（0 代码改动，拿到真实数据）

### Task A1: RAGAS 在供应链 KB 上全量评估

**目标：** 用 473 chunks 供应链知识库替换 IT KB，跑出面试能报的四大指标。

**执行：** `backend/eval/run_ragas_eval.py`（确保 TEST_QA_PAIRS 是供应链 QA 对）

**验证：** 产出新的 `eval_report_supplychain.md`，包含 F / CP / CR / AR

### Task A2: IR 指标全量 benchmark

**目标：** 在供应链 KB 上跑 Precision@K / Recall@K / MRR / NDCG@K，产出完整报告。

**执行：** `backend/eval/test_ir_metrics.py` + `rebuild_and_tune.py`

**验证：** 产出新的 `eval_ir_metrics_report_supplychain.md`

---

## Task B: 新增埋点（需写代码）

### Task B1: TTFT + 延迟拆解中间件

**目标：** 在 chat.py SSE 端点中插入计时标记，记录：
- `t_retrieval_ms`：向量检索耗时
- `t_llm_first_token_ms`：首 token 到达时间（TTFT）
- `t_total_ms`：总耗时

**文件修改：** `backend/app/api/chat.py`

**方法：** 在 event_generator 中用 `time.perf_counter()` 打点，随 `done` 事件或 `metrics` 事件透出给前端。

**验证：** 前端收到 `{type: "metrics", ttft_ms: 1234, total_ms: 5678}`

### Task B2: Token 成本统计端点

**目标：** 新增 `GET /api/v1/metrics/tokens` 端点，返回：
- `total_queries`: 历史查询总数
- `total_tokens`: 累计 token 消耗
- `avg_tokens_per_query`: 平均每次 token 数
- `total_cost_rmb`: 累计费用（元）

**文件新增/修改：**
- `backend/app/api/metrics.py`（新增端点）
- `backend/app/main.py`（注册 router）
- 在 chat.py 每次查询结束时写入 Redis 计数

**验证：** `curl localhost:8001/api/v1/metrics/tokens` 返回 JSON

### Task B3: QPS 计数器

**目标：** 轻量级请求计数器，每分钟统计一次。
- `GET /api/v1/metrics/summary` → `{qps_1m: 3.2, total_requests_24h: 847}`

**文件修改：** `backend/app/api/metrics.py`，用 Redis INCR + EXPIRE

---

## Task C: 整合展示

### Task C1: 更新 interview-showcase.html 指标速查表

**目标：** 用 Task A 跑出的真实数据替换占位值。

### Task C2: 新增监控 Dashboard HTML（可选）

**目标：** `docs/monitor-dashboard.html`，实时展示 TTFT/Token/QPS/满意度。

---

## 实施优先级

**第一轮（1-2h，面试立即可用）：**
- Task A1 + A2：重跑 benchmark，拿到可报的真实数字
- Task C1：更新 HTML 中的数据

**第二轮（2-3h，面试加分项）：**
- Task B1：TTFT + 延迟拆解
- Task B2：Token 成本统计端点

**第三轮（可选、锦上添花）：**
- Task B3：QPS 计数
- Task C2：监控 Dashboard

---

## 当前可报 vs 计划补充

```
检索模块 ✅
  Recall@K       → [需重跑]
  Precision@K    → [需重跑]
  MRR            → [需重跑]
  NDCG           → [需重跑]
  Context Prec   → [需重跑]
  Context Recall → [需重跑]

生成模块 ✅  
  Faithfulness   → [需重跑]
  Answer Relev   → [需重跑]

系统工程 ❌ → 🛠️
  TTFT           → [Task B1]
  总延迟          → [Task B1]
  Token 成本      → [Task B2]
  QPS            → [Task B3]

业务 ✅ (API 已有)
  用户满意度       → [API 存在，GET /feedback/stats]
```