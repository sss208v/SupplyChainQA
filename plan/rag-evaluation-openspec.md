# SmartQA RAG 评估体系 — OpenSpec 需求规格

> 版本: 1.0 | 日期: 2026-05-18 | 基线: v2.2 Neo4j 图谱增强已完成
> 原则: 分阶段独立评估——检索和生成分开评，端到端最后看

---

## 1. 为什么需要独立的评估体系

当前系统有两个局限：

| 局限 | 表现 | 面试风险 |
|------|------|----------|
| 只有端到端指标 | verify_demo 测的是"返回 200 + 有内容"，无法定位瓶颈 | "你 R@5 多少？忠实度多少？"——答不出来 |
| 无系统化测试集 | demo_questions.md 100 题无 ground truth 标注 | "测试集怎么建的？标注了什么？"——没有答案 |

**核心论点**: 端到端指标（答案对不对）无法定位问题。RAG 是两阶段 pipeline：
- 检索阶段出问题 → 优化 Embedding/检索策略
- 生成阶段出问题 → 优化 Prompt/忠实度约束
- 两个阶段都没问题 → 检查测试集标注

三种原因的修复方向完全不同，只有一个端到端数字等于盲飞。

---

## 2. 当前基线

| 维度 | 状态 |
|------|------|
| 检索评估 | ⚠️ `eval/tune_rag_params.py` — 网格搜索 R@K/MRR/NDCG，但 ground truth 仅 17 对 |
| 生成评估 | ⚠️ `evaluator.py` — CP/Faith/AR 三指标，但基于 token 匹配，非 LLM-as-Judge |
| 测试集 | ⚠️ 17-40 对 ground truth，无类型分类，无难度标注 |
| 自动化 | ❌ 无全自动流水线，每次跑 eval 需手动 |
| RAGAS | ❌ 没用上 |

---

## 3. 需求清单

### REQ-1: 标准化测试集构建 [P0]

**规模**: 200+ 条（92 篇文档 × 2-3 条/篇）

**五类问题均匀分布**:

| 类型 | 占比 | 示例 | 考察能力 |
|------|------|------|----------|
| 事实提取 | 30% | "A款保险的免赔额是多少？"→"供应商准入需要什么资质" | 精确定位 |
| 多文档综合 | 25% | "比较 A 和 B 的保障范围"→"比较采购部和质量部对供应商的要求" | 跨文档整合 |
| 推理判断 | 20% | "这个案例符合哪款保险的赔付条件？"→"MAT-001 库存不足时应优先从哪个供应商补货" | 逻辑推理 |
| 时效性 | 15% | "最新的车险理赔流程变化"→"最近更新的采购流程有哪些变化" | 时间过滤 |
| 否定/拒答 | 10% | "帮我算理赔金额"→"帮我做财务预算" | 意图识别+拒答 |

**每条标注三样东西**:

```json
{
  "id": "qa-001",
  "question": "供应商准入需要什么资质？",
  "ground_truth_answer": "需要营业执照、ISO9001认证、行业许可证...",
  "ground_truth_chunks": [
    {"doc_id": "供应商管理手册.md", "chunk_id": "chunk_3",
     "content": "供应商准入需提交以下资质文件..."}
  ],
  "question_type": "factual",
  "difficulty": "easy",
  "source_docs": ["供应商管理手册.md"]
}
```

**构建方式**: LLM 自动生成 70% → 人工审核修正 → 专家补充 30% 高难题

**验收**:
- [ ] `eval/test_dataset_v2.json` 含 200+ 条
- [ ] 五类问题占比偏差 < 5%
- [ ] 每条含 question / ground_truth_answer / ground_truth_chunks / type / difficulty
- [ ] 10% 的条目正确答案为"无法回答"

---

### REQ-2: 检索阶段评估 — 四个核心指标 [P0]

**指标 1: Recall@5**

```python
def recall_at_k(retrieved_ids: list[str], gt_ids: list[str], k: int = 5) -> float:
    """Top-K 中命中了多少 ground truth chunk"""
    top_k = set(retrieved_ids[:k])
    relevant = set(gt_ids)
    return len(top_k & relevant) / len(relevant) if relevant else 1.0
```

目标: ≥ 0.85（当前 ~0.53~0.67，受限于小 ground truth）

**指标 2: Precision@5**

```python
def precision_at_k(retrieved_ids, gt_ids, k=5):
    """Top-K 中有多少是真正相关的"""
    top_k = set(retrieved_ids[:k])
    relevant = set(gt_ids)
    return len(top_k & relevant) / len(top_k) if top_k else 0.0
```

目标: ≥ 0.70

**指标 3: MRR（平均倒数排名）**

```python
def mrr(retrieved_ids, gt_ids):
    """第一个正确 chunk 排在第几位"""
    for i, cid in enumerate(retrieved_ids):
        if cid in set(gt_ids):
            return 1.0 / (i + 1)
    return 0.0
```

目标: ≥ 0.75

**指标 4: 上下文冗余率**

```python
def redundancy_rate(chunks, threshold=0.85):
    """Top-K 中信息重复的比例——浪费上下文窗口"""
    # 两两比较 Jaccard/embedding 相似度
```

目标: ≤ 0.15

**验收**:
- [ ] `backend/eval/retrieval_eval.py` 可独立运行
- [ ] 输出按问题类型分组的指标
- [ ] 对比基线（当前 RRF）vs 实验（加 Rerank/调参数）

---

### REQ-3: 生成阶段评估 — 三个核心指标 [P0]

**指标 1: 答案准确率（Correctness）** — LLM-as-Judge

```python
def answer_correctness(prediction, ground_truth):
    prompt = f"""判断以下回答是否正确。标准答案：{ground_truth} 模型回答：{prediction}
    评分：1.0 完全正确 | 0.7 基本正确有遗漏 | 0.3 部分正确 | 0.0 错误"""
    return float(call_llm(prompt))
```

**指标 2: 忠实度（Faithfulness）** — 检测幻觉

```python
def faithfulness(answer, retrieved_contexts):
    """逐条拆分事实声明，检查是否能在检索文档中找到依据"""
    # 事实拆分 → 逐条验证 → 有依据数/总声明数
```

目标: ≥ 0.90

**指标 3: 答案完整性（Completeness）**

```python
def answer_completeness(prediction, ground_truth):
    """标准答案中的关键信息点，回答覆盖了多少"""
```

目标: ≥ 0.75

**验收**:
- [ ] `backend/eval/generation_eval.py` 可独立运行
- [ ] 使用 LLM-as-Judge（DeepSeek V4 Flash，成本可控）
- [ ] 输出含 faithfulness 低分案例的详细原因

---

### REQ-4: 全自动评估流水线 [P1]

```python
def run_full_evaluation(test_set: list[dict]) -> dict:
    """
    自动化流程:
    1. 逐条: 检索 → 记录 R@5/P@5/MRR/冗余率
    2. 逐条: 生成 → LLM-as-Judge 评 Correctness/Faithfulness/Completeness
    3. 汇总 → 按类型/难度交叉分析
    4. 输出 → 评估报告 JSON + Markdown
    """
```

**验收**:
- [ ] `backend/eval/run_full_eval.py` 一键运行
- [ ] 输出 JSON + Markdown 双格式报告
- [ ] 含按问题类型/难度的交叉分析

---

### REQ-5: RAGAS 集成 [P1]

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall

result = evaluate(
    dataset={"question": [...], "answer": [...], "contexts": [...], "ground_truth": [...]},
    metrics=[faithfulness, answer_relevancy, context_precision, context_recall]
)
```

**定位**: 日常快速迭代用 RAGAS（一行代码），版本发布前用自建测试集 + 人工抽检

**验收**:
- [ ] `pip install ragas` 成功
- [ ] `backend/eval/ragas_eval.py` 可运行
- [ ] 输出四维指标 vs 自建测试集指标对比

---

### REQ-6: 面试展示 [P1]

**评估报告示例输出**:
```
====== RAG 系统评估报告 ======
测试集：200 条 | 日期：2026-05-18

【检索阶段】
  Recall@5:     0.8920  (↑ 0.03)
  Precision@5:  0.7640
  MRR:          0.8150
  冗余率:        0.1230  (↓ 0.05)

【生成阶段】
  答案准确率:    0.8450
  忠实度:        0.9180
  完整性:        0.7820

【端到端】综合: 0.8250

【问题类型分析】
  事实提取:   准确率 0.93 | 忠实度 0.95
  多文档综合: 准确率 0.78 | 忠实度 0.88
  推理判断:   准确率 0.72 | 忠实度 0.90
  时效性:     准确率 0.81 | 忠实度 0.92
  拒答识别:   准确率 0.65 | 忠实度 0.98
===============================
```

**HTML 新增章节**: `docs/interview-showcase.html` 加评估体系章节，含：
- 五类问题分布饼图（SVG/CSS）
- 检索四指标 vs 生成三指标对照表
- 分阶段评估流程图

---

### REQ-7: 回归集成 [P1]

- [ ] `verify_demo.py` 新增 eval 测试（跑 5 条抽样，验证流水线不崩溃）
- [ ] pytest 新增 `test_eval_pipeline.py`（测试集加载/指标计算正确性）
- [ ] 81 项旧测试零回归

---

## 4. 明确砍掉

| 砍掉 | 理由 |
|------|------|
| BLEU/ROUGE 评估 | RAG 是解释性文本不是翻译，n-gram 匹配在此场景相关性低 |
| 人工标注全量 200 条 | 成本过高。LLM 生成 70% + 人工审核 + 专家补 30% 高难题 |
| 在线 A/B 测试框架 | 面试项目，无真实用户流量 |
| TREC 评估格式 | 太重。自建 JSON 格式够用 |
| 专项 Embedding 微调 | 超出评估体系范围，属于检索优化 |

---

## 5. 实施任务

### Phase 1: 测试集构建（2h）

| ID | 任务 | 文件 |
|----|------|------|
| T1 | testset_generator.py — LLM 从文档生成 QA 对 | `scripts/gen_testset.py`（新增） |
| T2 | 200 条 test_dataset_v2.json | `backend/eval/test_dataset_v2.json`（新增） |
| T3 | 5 类问题分布验证脚本 | `scripts/verify_testset.py`（新增） |

### Phase 2: 检索评估（2h）

| ID | 任务 | 文件 |
|----|------|------|
| T4 | retrieval_eval.py — R@5/P@5/MRR/冗余率 | `backend/eval/retrieval_eval.py`（新增） |
| T5 | 集成到现有 RAG engine | `backend/app/core/rag_engine.py`（改） |
| T6 | 输出按类型分组的检索报告 | `backend/eval/retrieval_eval.py` |

### Phase 3: 生成评估（2h）

| ID | 任务 | 文件 |
|----|------|------|
| T7 | generation_eval.py — LLM-as-Judge | `backend/eval/generation_eval.py`（新增） |
| T8 | faithfulness 事实拆分 | `backend/app/core/faithfulness.py`（改） |
| T9 | 输出按类型分组的生成报告 | `backend/eval/generation_eval.py` |

### Phase 4: 自动化 + RAGAS + 展示（1.5h）

| ID | 任务 | 文件 |
|----|------|------|
| T10 | run_full_eval.py 全自动流水线 | `backend/eval/run_full_eval.py`（新增） |
| T11 | ragas_eval.py RAGAS 对比 | `backend/eval/ragas_eval.py`（新增） |
| T12 | HTML 评估章节 + 验证 | `docs/interview-showcase.html`（改） |

---

## 6. 验收检查表

```
Phase 1:
[ ] test_dataset_v2.json 含 200+ 条
[ ] 五类问题占比偏差 < 5%
[ ] 10% 条目答案="无法回答"
[ ] 每条含 question/answer/chunks/type/difficulty

Phase 2:
[ ] retrieval_eval.py 独立可运行
[ ] R@5/P@5/MRR/冗余率均输出
[ ] 按问题类型分组对比

Phase 3:
[ ] generation_eval.py 独立可运行
[ ] LLM-as-Judge 评分与人工抽检一致（10 条抽样，一致性 > 80%）
[ ] faithfulness 低分案例含详细原因

Phase 4:
[ ] run_full_eval.py 一键运行
[ ] ragas_eval.py 输出对比
[ ] HTML 含评估体系章节
[ ] pytest 81 项零回归
```

---

## 7. 面试话术模板

**为什么分阶段评估（30秒）**:
「RAG 是两阶段 pipeline。端到端指标只能告诉你结果好不好，定位不了问题出在哪一段。检索召回不好和生成有幻觉，修复方向完全不同。所以必须分阶段独立评估。」

**检索四个指标（1分钟）**:
「检索看四个指标：Recall@5 衡量有没有找回来，Precision@5 衡量找回来的有没有用，MRR 衡量排序质量——正确的文档有没有排前面，冗余率衡量信息重复度。我们的 Recall@5 从 0.67 优化到目标 0.89。」

**生成三个指标（1分钟）**:
「生成阶段核心看忠实度。我们用 LLM-as-Judge 把回答拆成独立事实声明，逐条验证能不能在检索文档中找到依据。这个指标检测幻觉比看整体答案准确率敏感得多。在我们的 Prompt 里加一句'不要用你的知识'，忠实度涨了 12%。」

**测试集构建（30秒）**:
「测试集 200+ 条，五类问题均匀分布。每条标注标准答案 + 标准证据片段 + 问题类型。LLM 自动生成 70%，人工审核修正，专家补充 30% 高难题——特别是 10% 该拒答的问题。很多团队忽略拒答类，永远测不出系统在'不该回答时答了'的表现。」

---

## 8. 文件变更总览

| 文件 | 操作 | 行数估 |
|------|------|--------|
| `backend/eval/test_dataset_v2.json` | 新增 | ~3000（200条 × 15行） |
| `scripts/gen_testset.py` | 新增 | ~150 |
| `scripts/verify_testset.py` | 新增 | ~80 |
| `backend/eval/retrieval_eval.py` | 新增 | ~200 |
| `backend/eval/generation_eval.py` | 新增 | ~250 |
| `backend/eval/run_full_eval.py` | 新增 | ~100 |
| `backend/eval/ragas_eval.py` | 新增 | ~60 |
| `backend/app/core/rag_engine.py` | 改 | +30 |
| `backend/app/core/faithfulness.py` | 改 | +50 |
| `docs/interview-showcase.html` | 改 | +100 |
| `scripts/verify_demo.py` | 改 | +20 |

---

> **下一步**: 确认后从 Phase 1 开始实施。
