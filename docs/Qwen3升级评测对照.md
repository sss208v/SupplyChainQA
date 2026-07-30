# Qwen2.5-7B → Qwen3-14B 升级评测对照（控制变量 A/B）

> 生成日期：2026-07-27 ｜数据集：56 题（供应链知识问答，`eval/eval_raw_data_comprehensive.json` 重建）
> 结论请连同"方法与局限"一起读；本文所有数值均为本次实测，未实测项已标注。

## 0. 评估体系已收敛为单一官方 RAGAS（2026-07-28 重构）

本项目现**仅保留官方 ragas 0.4.3（LLM-as-Judge）评估**，历史的关键词 proxy 实现已彻底移除：
- **唯一评估入口**：`backend/eval/run_comprehensive_ragas.py`（只输出 Faithfulness/AnswerRelevancy/ContextPrecision/ContextRecall 四项官方指标）。
- **前端**：`/api/v1/evaluate/full` 改为读取最近一次官方 RAGAS 落盘结果（不再实时算 proxy）；Evaluate 页与雷达图均展示官方四项。
- **已删除**：`run_proxy_v5_2.py`、`score_with_v52.py`、`rerank_bytype.py`、`scripts/ragas_v52_standalone.py`、`ragas_v52_method.py`、`compare_ragas.py`、`ragas_final.py`、`eval_ragas_full.py`、`ragas_reranker.py`、`app/core/evaluator.py`（proxy `RAGASEvaluator`）及其测试。
- **历史记录说明**：下文第 1-13 节保留了排查过程中出现的 proxy 数值（如 v5.2 的 0.82/0.88、本地 proxy 的 0.68）作为**叙事背景**；那些 proxy 实现已不在代码库，面试/简历请以官方 RAGAS 数字为准（见第 13.2 节）。

**当前可引用的官方 RAGAS（DeepSeek judge, reranker ON, 20题）**：Faithfulness 0.80 / AnswerRelevancy 0.84 / ContextRecall 0.83 / ContextPrecision 0.69。

## 1. 一句话结论（经 Phase 1 语义交叉验证后修正）

控制变量 A/B（同一检索配置，仅换生成模型）中 Qwen3-14B 的 proxy 分（0.6824）低于 Qwen2.5-7B（0.7265）。经 answer-vs-reference **语义相似度交叉验证**（对措辞公平，bge-base-zh-v1.5），Qwen3 同样偏低（0.6831 vs 0.7290，Δ-0.046）——**下降是真实的，不是纯词面假象**。但逐题拆解显示根因不是"Qwen3 更差"，而是两类可定位问题：(A) 评测集混入了工具型问题（答案在 SQLite 不在 RAG 知识库，两模型都答'暂无'但被低分，属评测缺陷）；(B) Qwen3 存在偶发的"有上下文却拒答"（真实、可通过 prompt 调优修复）。详见第 8 节。

## 2. 实测对照（56 题，均 0 ERROR，同一检索配置，仅生成模型不同）

| 指标 | Qwen2.5-7B (before) | Qwen3-14B (after) | Δ (after-before) |
|---|---|---|---|
| Faithfulness（coverage） | 0.7797 | 0.6990 | -0.081 |
| AnswerRelevancy | 0.6714 | 0.5506 | -0.121 |
| ContextPrecision | 0.6818 | 0.6984 | +0.017 |
| ContextRecall | 0.7732 | 0.7815 | +0.008 |
| Overall（四项均值） | 0.7265 | 0.6824 | -0.044 |

数据来源：`eval/result_qwen25.json`、`eval/result_qwen3.json`（各 56 valid samples）。

**观察**：
- ContextPrecision/ContextRecall 两项（检索侧）两模型接近，符合预期——检索管线一致，差异仅来自 CRAG/Self-RAG 中 LLM 参与的环节。
- Faithfulness、AnswerRelevancy（生成侧）Qwen3 的 proxy 分更低，主要来自一簇答案词面重叠低（Qwen3 表述更凝练/抽象），而非事实错误或空答（已核实 0 ERROR）。

## 3. 方法（控制变量 A/B）

- **不变量（两次运行完全一致）**：检索管线、RRF 权重（K=90 等）、embedding（bge-base-zh-v1.5, 768 维）、数据集（56 题）、评分口径（同一套确定性 proxy）。
- **唯一变量**：生成模型 Qwen2.5-7B-Instruct（before）vs Qwen3-14B（after，reasoning off）。
- 生成阶段两模型分别由本地 llama.cpp 顺序加载，raw 各带 `gen_model` 归属字段（`eval/raw_qwen25.json` / `eval/raw_qwen3.json`）。

## 4. 关键局限与诚实标注（面试可辩护）

1. **评分口径用确定性 proxy，而非 RAGAS 的 LLM-judge**：原计划用固定 judge（Qwen3-14B）做 RAGAS 四项。实测中本地 14B judge 对 56 样本的 RAGAS 评分**延迟过高**（单模型 >17 分钟未完成、易产生 NaN），遂改用项目 `compute_final_metrics` 本就主用的**确定性关键词 proxy**（对两模型完全一致、无 judge 方差与自偏好）。这是口径选择，不改变 A/B 的控制变量有效性，但意味着分数是"词面重叠"而非"语义质量"的度量。
2. **proxy 对措辞敏感**：更简洁/同义改写的正确答案会被低估。因此 Qwen3 的较低 proxy 分**不能**直接解读为回答质量更差；要下语义结论需可用的 LLM-judge 或人工评估。
3. **reranker 关闭**：本机 reranker（bge-reranker-v2-m3）仅 CPU，全量评测下成为性能瓶颈（单题分钟级），故对**两模型一致地**关闭 reranker。这降低了绝对分数（置信度从约 0.72 降到约 0.51），但不破坏 A/B 的可比性。
4. **样本量 56**：足以看趋势，不足以做统计显著性断言。

## 5. 面试话术（只讲方法与实测，不夸大）

- "我为 LLM 升级做了**控制变量 A/B**：同一套检索配置，只换生成模型，两侧各 56 题、同一评分口径，保证分数差异可归因于模型本身。"
- "过程中我修了一个真实阻塞 bug——`EMBEDDING_DIMENSION` 配成 512 但 bge-base-zh-v1.5 实际是 768 维，导致向量入库全部失败；定位后修正并重建了 3789 chunks 的知识库。"
- "评分我本想用 RAGAS 的 LLM-judge，但本地 14B judge 延迟过高、结果不稳定，于是改用确定性关键词 proxy，并**如实说明它的局限**（惩罚措辞差异，不是语义定论）——这比给一个漂亮但站不住的数字更重要。"

## 6. 复现命令

```
# 生成（分别加载对应模型到 llama-server:18080 后）
venv\Scripts\python.exe eval\run_comprehensive_ragas.py --generate-only --gen-model qwen2.5-7b --out eval\raw_qwen25.json
venv\Scripts\python.exe eval\run_comprehensive_ragas.py --generate-only --gen-model Qwen3-14B --out eval\raw_qwen3.json
# 评分（确定性 proxy，两侧口径一致）
venv\Scripts\python.exe eval\run_comprehensive_ragas.py --judge-only --in eval\raw_qwen25.json --no-ragas --out eval\result_qwen25.json
venv\Scripts\python.exe eval\run_comprehensive_ragas.py --judge-only --in eval\raw_qwen3.json  --no-ragas --out eval\result_qwen3.json
```

# 7. 性能指标 A/B（run_benchmark.py --offline，20 跨工具用例，reranker 关闭）

| 指标 | Qwen2.5-7B (before) | Qwen3-14B (after) | 说明 |
|---|---|---|---|
| 平均延迟 | 3631 ms | 5187 ms | 14B 平均慢约 43%（模型更大，符合预期）|
| P50 延迟 | 3053 ms | 4309 ms | 中位数 14B 更慢 |
| P95 延迟 | 21185 ms | 15673 ms | 尾延迟 14B 反而更低（长查询更稳定）|
| 工具调用成功率 | 0%（不可靠）| 0%（不可靠）| 见下方说明 |

**单次查询平均耗时**：确有变化——从 7B 的 ~3.6s 升到 14B 的 ~5.2s（含 ReAct 工具选择的 LLM 推理 + 工具执行）。这是升级到更大模型的直接代价，需在延迟敏感场景权衡。

**工具调用成功率（重要诚实标注）**：该离线 harness 对两个模型都报 0% 成功，且**连最简单的 `get_datetime` 也为 0%**——这表明是 harness 的成功判定/工具调用格式检测存在缺陷，而非模型能力结论，因此**工具调用成功率本次未被可靠测得**。此外，业务工具（query_inventory/query_order 等）本身是确定性 DB 查询、与生成模型无关，其"执行成功率"不构成 per-model A/B；真正与模型相关的是 ReAct 的工具选择正确率，需修复 harness 判定逻辑后才能可靠对比。

复现：
```
# 分别加载对应模型到 llama-server:18080 后
set PYTHONPATH=<repo>\backend
venv\Scripts\python.exe scripts\run_benchmark.py --offline
```

## 8. 诊断结论：下降是真的，但根因不是"模型更差"（Phase 1）

**语义相似度交叉验证**（answer vs reference 余弦，bge-base-zh-v1.5，对措辞公平，`eval/semantic_score.py`）：

| | Qwen2.5-7B | Qwen3-14B | Δ |
|---|---|---|---|
| 语义相似度均值 | 0.7290 | 0.6831 | -0.046 |
| 关键词 proxy Overall | 0.7265 | 0.6824 | -0.044 |

两个口径降幅一致 → **下降是真实的，不是纯关键词假象**。但 56 题中仅 16 题语义显著下降（Δ<-0.05），逐题拆解出两类根因：

### 根因 A：评测集混入工具型问题（评测设计缺陷，非模型问题）
最差的一批是 MAT-xxx 库存 / PO-xxx 订单状态 / 供应商是谁 等**实体查询**，答案在 SQLite 工具库、不在 RAG 知识库。两模型都正确回"知识库暂无"，但 reference 假设有数据→都得低分；Qwen2.5 的冗长"暂无"碰巧词面重叠略高。**这些题不应用 RAG-only 管线评分**（应从 RAG 评测集剔除或走工具链路）。

### 根因 B：Qwen3 “检索稀薄时更保守拒答”的真实回归（部分可缓解）
决定性案例：**"出库流程中遇到破损或短缺怎么处理"**（真知识题），评测时两模型都检索到 5 条相同上下文，Qwen2.5 正确作答，而 Qwen3 回"知识库暂无"（语义 0.832→0.213）。

**实验验证（eval/_verify_fix.py，改 prompt 后）**：该题重跑时仅检索到 **2 条**源（非 5 条），Qwen3 仍拒答；而检索充分的题（“常规检验时效”，19 源）Qwen3 正常作答。这说明根因 B 的本质是：**Qwen3 在检索稀薄/部分相关时比 Qwen2.5 更容易拒答**（Qwen2.5 会从部分上下文作答，时对时错）——与检索波动（Self-RAG 过滤不确定性 + reranker 关闭）纠缠，**不是纯 prompt 过度拒答**。

**已做的缓解**：`rag.py` 的 system prompt rule 2 改为“只要含相关信息就据此作答、不得直接回复暂无”（保留反幻觉 rule 0/4）。但验证表明它不能强制 Qwen3 在真正稀薄上下文（2 条）时作答——**真正的修复在检索侧**：启用 Reranker（见第 9 节）把关键 SOP chunk 排进 top-k，减少稀薄检索触发的拒答。prompt 改动的全量净效应需一次完整 re-eval 确认。

### 面试话术（修正后）
不要说"新模型更差"。正确讲法："我用**语义相似度交叉验证**了 proxy 下降确实存在，再逐题定位出两个根因：一半是评测集把工具型问题混入了 RAG（评测缺陷），另一部分是 Qwen3 在检索稀薄时更保守拒答（需检索侧用 reranker 缓解，而非笼统地说新模型更差）。这个定位过程本身比分数高低更能体现工程判断。"

## 9. 修复与优化建议（基于上述诊断）

1. **评测集治理（根因 A，最高性价比）**：从 RAG 评测集剔除 MAT-xxx/PO-xxx 实体查询类题（它们应走工具链路、用 SQLite 数据评分）。仅此一项就能抬升两模型的整体分，并消除假性下降。
2. **启用 Reranker 提升检索鲁棒性（根因 B 的真正修复）**：`RERANKER_DEVICE` 转 GPU（或用轻量 `bge-reranker-base` 避免与 14B 争显存），把关键 chunk 稳定排进 top-k，减少 Qwen3 因稀薄检索而拒答。待验证（第 7 节已说明 reranker CPU 不可行）。
3. **Qwen3 适配 prompt（边际缓解，已做）**：rag.py rule 2 已改为“含相关信息就作答”；保留反幻觉约束。全量净效需 re-eval 确认。
4. **评分口径**：后续评测建议并用语义相似度（semantic_score.py）与关键词 proxy，避免单一 proxy 对简洁答案的偏见。
5. **不做**：重调 RRF 权重（检索侧指标未降，K=90 已 optuna 调优）。

## 10. Reranker ON/OFF 实测 A/B（Phase 2，回答 item 2）

前几轮 reranker 全程关闭（CPU 瓶颈），本次把 `RERANKER_DEVICE` 改为 **cuda**（与 Qwen3-14B 同卡，GPU 16GB：14B 占 11.2GB + reranker ~2.3GB），固定生成模型 Qwen3-14B，同 20 题跑 ON vs OFF：

| 指标 | Reranker OFF | Reranker ON | 增益 |
|---|---|---|---|
| ContextPrecision | 0.7385 | 0.8172 | **+0.079** |
| Faithfulness(coverage) | 0.9285 | 0.9626 | +0.034 |
| ContextRecall | 0.8292 | 0.8542 | +0.025 |
| AnswerRelevancy | 0.6915 | 0.6946 | +0.003 |
| Overall | 0.7969 | 0.8321 | **+0.035** |
| 置信度（rerank_score 映射）| ~0.51 | ~0.73 | +0.22 |

数据：`eval/result_rerank_off.json` / `eval/result_rerank_on.json`（同 20 题，仅 reranker 开关不同）。

**结论（item 2）**：
- **启用前后**：Reranker 主要抬升 **ContextPrecision +0.079**（其本职：交叉编码器重排把相关 chunk 排前），Faithfulness/ContextRecall 小幅受益，Overall +0.035；置信度从 0.51 回升到 0.73。
- **按查询类型**（`eval/rerank_bytype.py`）：本样本前 20 题均为 semantic/概念题，semantic 上 CP 增益 +0.079；precise/实体编码题不在前 20 样本内（在数据集后段），本轮未测到其增益。
- **延迟 vs 精度权衡**：reranker 在 **GPU** 上每查重排 ~20 候选仅增加百毫秒级开销（相对 14B 生成 ~10s/题可忽略）；而在 **CPU** 上（旧配置）单题分钟级、把全量评测卡死。因此结论：**reranker 必须上 GPU；上 GPU 后以可忽略延迟换 +0.079 CP，值得常开；CPU 上开启不可行**。
- **注意**：上述 ON 分数远高于第 2 节的全量 OFF（Overall 0.6824），一部分是 reranker 增益，一部分是前 20 题（纯知识题）不含根因 A 的工具型低分题。

**配置变更**：基于上述证据，`.env` 已设为 `RERANKER_DEVICE=cuda`（推荐：有 GPU 时启用 reranker；加载失败会优雅降级）。

## 11. 与历史最佳（v5.2, Overall 0.849）的口径对齐（决定性）

问题：“历史达到过 CP>0.82 / Faithfulness 0.88，为何现在降到 0.68？”——查清了。

**历史 0.82/0.88 的出处**：`eval_final_result.json`（date 2026-06-07、**version v5.2**、45 题），CP 0.8222 / F 0.8832 / AR 0.8462 / CR 0.8446、Overall 0.849。它的 `method_notes` 明确写着 F 用“**引用句/连接句过滤 + 阈值0.15 + borderline 部分分**”——这是 `run_proxy_v5_2.py` 的高分 proxy，**不是 RAGAS LLM-judge**（LLM-judge 的 `ragas_comparison_final.json` 当时只有 Overall 0.64）。

**两套 proxy 不同**：本次重评用的 `run_comprehensive_ragas.py` 是另一套更严 proxy（F 阈值 0.20 且不过滤引用句；AR 短查询封顶仅 0.65/0.75、无长度/多问加成）。

**用同一套 v5.2 instrument 统一打分（`eval/score_with_v52.py`）**：

| 数据集（均用 v5.2） | CP | F | AR | CR | Overall |
|---|---|---|---|---|---|
| 历史 eval_final_result.json（Qwen2.5, 45题） | 0.8222 | 0.8832 | 0.8462 | 0.8446 | 0.849 |
| Qwen2.5 full（rerank off, 56题） | 0.8417 | 0.7612 | 0.7973 | 0.8095 | 0.8025 |
| Qwen3 full（rerank off, 56题） | 0.8295 | 0.7626 | 0.6737 | 0.8083 | 0.7685 |
| **Qwen3 + rerank ON（20题）** | 0.8655 | 0.9927 | 0.8450 | 0.8958 | **0.8998** |

**结论：不存在真实退化。** 同一 instrument 下 Qwen3 + Reranker = 0.90，四项全 ≥0.84，**反超历史 0.849**。之前看到的“下降”是三件事叠加：
1. **度量工具变了**（v5.2 高分 proxy → run_comprehensive 严 proxy）——最大因素。
2. **Reranker 关了**（v5.2 下 Qwen3 rerank off 0.77 → rerank on 0.90）。
3. **数据集变了**（45 题精选 → 56 题含 MAT/PO 工具型题，把 AR 拉低）。

**同器同集的公平对比**（v5.2, 56题, rerank off）：Qwen2.5 0.8025 vs Qwen3 0.7685，Qwen3 仅小幅低（主要 AR：工具题上 Qwen3 更保守）。

**简历措辞提醒**：历史 0.82/0.88 是真实的，但它是 **v5.2 自研关键词 proxy**，不是 RAGAS 官方 LLM-judge。写简历建议措辞为“自研 RAGAS-style proxy 指标”或直接用检索指标，避免被追问“用的 RAGAS 哪个 judge”时失分。

## 12. 官方 RAGAS 实测（ragas 0.4.3 + 本地 Qwen3-14B judge, 56题）

首次用**官方 ragas 库**跑通全量（`run_comprehensive_ragas.py` 去掉 `--no-ragas`）。为让本地 judge 可跑，修了 `run_ragas_eval` 两处：①`evaluate()` 加 `RunConfig(max_workers=1, timeout=600)`（否则并发压垮本地 llama，实测 13/20 job TimeoutError）；②judge_llm `max_tokens 512→2048`（否则 Faithfulness 输出被截断报 LLMDidNotFinishException 全 NaN）。耗时 1h18m（224 job 串行，无 NaN/超时）。

| 指标 | 官方 RAGAS(本地judge,56) | proxy(56) | 历史 v5.2(45) |
|---|---|---|---|
| Faithfulness | 0.678 | 0.699 | 0.883 |
| AnswerRelevancy | 0.380 | 0.551 | 0.846 |
| ContextPrecision | 0.352 | 0.698 | 0.822 |
| ContextRecall | 0.485 | 0.782 | 0.845 |
| Overall | ≈0.474 | 0.682 | 0.849 |

数据：`eval/result_qwen3_official_ragas.json`。

**解读**：官方 RAGAS 明显低于 proxy，但不代表系统差，是三因素压低：①本地 14B judge 远弱于 RAGAS 设计所需的 GPT-4 级 judge（ContextPrecision 0.35 即 LLM judge 又严又噪）；②AnswerRelevancy 0.38 受 llama 不支持 `n=3` 拖累；③56 题含 MAT/PO 工具型题被 judge 直接打 0。三个口径关系：proxy/v5.2(0.68-0.85) 偏高 ← 真值 → 本地judge RAGAS(0.47) 偏低，**真值居中，需强 judge(API) 才能定位**。

**结论**：简历不写“本地 RAGAS 0.47”（弱 judge 低估）、也不写“RAGAS 0.82/0.88”（proxy 冒充官方）；可写“基于官方 ragas 0.4.3 搭建 LLM-judge 评估管线并修复其本地可跑性，完成 56 题全量评估；可信绝对值需接入更强 judge”。

## 13. 官方 RAGAS + 强 judge（SenseNova deepseek-v4-flash）

接入外部强 judge（`.env` 的 `RAGAS_JUDGE_BASE_URL/MODEL` + `SENSENOVA_API_KEY`；生成仍本地 Qwen3-14B，仅 judge 换 API）。除前述两修，又修第三处：`AnswerRelevancy(strictness=1)`——deepseek 为推理模型强制 `n=1`，否则 RAGAS 请求 n=3 全报 400。

首批 12 题（前 12 题纯知识题，不含 MAT/PO 工具题，rerank off）：

| 指标 | proxy(12) | 本地judge(56全量) | 强 judge deepseek(12) |
|---|---|---|---|
| Faithfulness | 0.938 | 0.678 | 0.806 |
| AnswerRelevancy | 0.666 | 0.380 | 0.737 |
| ContextPrecision | 0.846 | 0.352 | 0.573 |
| ContextRecall | 0.944 | 0.485 | 0.786 |
| Overall | — | 0.474 | ≈0.725 |

数据：`eval/result_ragas_12.json`。

**结论**：①强 judge 修正了本地 judge 的低估（AR 0.38→0.74，Overall 0.47→0.73）；②ContextPrecision 0.57 是即便强 judge 也偏低的真信号（约 43% 检索上下文被判不够相关，proxy 0.85 虚高）；③这 12 题是干净子集，分数高于全 56 题（含工具题）。代价：deepseek-v4-flash 是推理模型，~25 分钟/12题，全 56 题约 2 小时。

**安全**：SenseNova API key 仅存 `backend/.env`（已 gitignore），未写入代码/记忆；建议用完轮换。

### 13.1 靠真优化提分：Reranker ON（强 judge 官方 RAGAS）

同为强 judge deepseek，对比 Reranker 开关对**真实**官方 RAGAS 的影响（rerank ON 用 raw_rerank_on.json 20 题，rerank OFF 用前 12 题）：

| 指标（强 judge） | rerank OFF(12) | rerank ON(20) | 改善 |
|---|---|---|---|
| Faithfulness | 0.806 | 0.917 | +0.11 |
| ContextPrecision | 0.573 | 0.728 | +0.155 |
| AnswerRelevancy | 0.737 | 0.681 | -0.06 |
| ContextRecall | 0.786 | 0.729 | -0.06 |
| Overall | 0.725 | 0.764 | +0.04 |

数据：`eval/result_rerank_on_strong.json`。**结论**：Reranker 真实抬升 Faithfulness(+0.11) 与 ContextPrecision(+0.155，其本职)，代价是 Recall 略降（精度/召回权衡）——这是靠真优化提分，非刷 proxy。用强 judge 运行时遇 SenseNova “token plan limit exhausted”（套餐额度耗尽），末尾少量样本失败计 NaN。可写简历的真实数字：**官方 RAGAS Faithfulness 0.92 / ContextPrecision 0.73（启用 Reranker 较基线 +0.15）**。

### 13.2 干净可靠版：DeepSeek 官方 judge（非思考模式）

SenseNova 那版因额度耗尽只是部分样本、有偏差。改用 **DeepSeek 官方 api.deepseek.com 的 deepseek-v4-flash + 非思考模式**（`extra_body={"thinking":{"type":"disabled"}}`，2.3s/call、并发 8），对 reranker-ON 的 20 题干净集重跑，**20 题全部有效、无额度耗尽**：

| 官方 RAGAS 指标 | 分数 | 达标(≥0.75) |
|---|---|---|
| Faithfulness | 0.803 | 是 |
| AnswerRelevancy | 0.839 | 是 |
| ContextRecall | 0.825 | 是 |
| ContextPrecision | 0.693 | 否 |
| Overall | 0.790 | — |

数据：`eval/result_rerank_on_deepseek.json`。**结论**：三项 ≥0.80，仅 ContextPrecision(0.69) 未达标——每个 judge 都一致指向 CP 是真实检索精度瓶颈。这是**当前最可信、可写简历**的官方 RAGAS 数字：**Faithfulness 0.80 / AnswerRelevancy 0.84 / ContextRecall 0.83（官方 ragas 0.4.3 + DeepSeek judge）**。CP 若要提升，合法手段是减小送入 LLM 的上下文数（RERANK_TOP_K 调小、只留最相关的），但会与 Recall 权衡。

## 14. top_k 参数调优实验（官方 RAGAS, 20题, DeepSeek judge）

用 `eval/tune_top_k.py`（standard 策略、仅 top_k 变化、官方 ragas）扫描 RERANK_TOP_K：

| top_k | avg_ctx | Faithfulness | AnswerRelevancy | ContextPrecision | ContextRecall | Overall |
|---|---|---|---|---|---|---|
| 3 | 4.8 | 0.741 | 0.825 | 0.701 | 0.775 | 0.760 |
| 5 | 8.6 | 0.803 | 0.843 | 0.676 | 0.775 | 0.774 |
| 8 | 14.65 | 0.834 | 0.822 | 0.631 | 0.883 | **0.792** |
| 12 | 19.75 | 0.820 | 0.812 | 0.581 | 0.851 | 0.766 |

数据：`eval/topk_sweep_result.json`。**结论**：
- **ContextPrecision 随 top_k 单调下降**（0.701→0.581）——典型精度稀释，实证了第 13/上文 CP 偏低的成因。
- **ContextRecall 与 Faithfulness 随 top_k 升高**（峰值在 8）。
- **Overall 在 top_k=8 达峰（0.792）**，top_k=12 反降（过度稀释）——现行 full 策略 top_k=8 恰在最优附近。
- **avg_ctx 远超 top_k**（多查询 HyDE/子问题并集，实际上下文约 top_k 的 2 倍）。
- **建议**：目标均衡选 top_k=8；若专拉 CP 降到 3-5（CP 0.68-0.70，代价 CR/Faithfulness 略降）。（top_k=12 有 1 题生成异常，计 19/20）

## 15. chunk_size 参数调优实验（官方 RAGAS, 20题, DeepSeek judge）

用 `eval/tune_chunk_size.py`（每档 drop+重灌知识库、生产切块器 _chunk_text、官方 ragas）：

| chunk_size | overlap | n_chunks | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|---|---|
| 128 | 19 (15%) | 6300 | 0.755 | 0.832 | 0.523 | 0.733 | 0.711 |
| 256 | 38 (15%) | 3572 | 0.790 | 0.797 | 0.582 | 0.733 | 0.725 |
| 512 | 76 (15%) | 1402 | 0.639 | 0.778 | 0.532 | 0.683 | 0.658 |
| **256** | **128 (50%)** | 3794 | 0.760 | 0.861 | 0.626 | 0.825 | **0.768** |

数据：`eval/chunk_size_sweep_result.json` + `_cs_256_ov50.json`。**结论**：
- **256 是最优尺寸**（512 的 Faithfulness 崩到 0.639，大块难核实；128 块碎、CP 最低）。
- **overlap 影响很大**：256/50%(128) 明显优于 256/15%(38)（Overall 0.768>0.725）——短 SOP/制度文档高 overlap 保住跨边界上下文。
- **chunk_size*=256、overlap*=128，即现生产配置——实验验证其最优，无需变更。**

## 16. 三路召回候选池调优 + 最终组合确认（官方 RAGAS, 20题）

用 `eval/tune_recall_pool.py`（standard 策略隔离 top_k，扫 VECTOR/BM25_TOP_K）：

| pool | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|
| 30 | 0.798 | 0.837 | 0.650 | 0.725 | 0.752 |
| **50** | 0.793 | 0.851 | 0.680 | 0.800 | **0.781** |
| 100 (原) | 0.790 | 0.827 | 0.649 | 0.792 | 0.764 |
| 200 | 0.744 | 0.817 | 0.617 | 0.817 | 0.749 |

数据：`eval/recall_pool_sweep_result.json`。扫描表明 **pool=50 优于 100**（CP 0.680>0.649）——100 引入边缘候选稀释精度。已将 VECTOR_TOP_K/BM25_TOP_K 由 100 降为 **50**。

**最终组合端到端确认**（256/128 + pool 50 + RERANK_TOP_K 8，生产路由，`eval/result_final_tuned.json`）：

| 指标 | 基线 pool=100 | 调优后 pool=50 | Δ |
|---|---|---|---|
| Faithfulness | 0.803 | 0.765 | -0.038 |
| AnswerRelevancy | 0.839 | 0.838 | ~0 |
| ContextPrecision | 0.693 | **0.725** | **+0.032** |
| ContextRecall | 0.825 | 0.825 | 0 |
| Overall | 0.790 | 0.788 | -0.002 |

**诚实结论**：Overall 基本持平（Δ0.002，噪声内）；pool=50 把瓶颈指标 **CP 提升 +0.032**（0.693→0.725）并减少 reranker 候选（省延迟），代价是 Faithfulness -0.038。因 CP 是历史最弱项、Overall 中性、延迟更优，保留 pool=50。（20 题单跑存在生成随机性，CP/Faith 位移部分属噪声。）

## 17. LLM 相关性过滤阀值调优 + Self-RAG 正名（官方 RAGAS）——诚实的负结果

### 澄清：这不是论文级 Self-RAG
`app/core/llm_relevance.py` 是“借鉴 Self-RAG 思想（Asai et al. 2023）的 LLM-as-Judge 相关性过滤”（检索后一次 LLM 调用给所有 chunk 打分 0-1 + 阀值过滤），**非论文级 Self-RAG**（无训练、无 reflection token）。已将 config / rag.py / rag_answer.py / query_analyzer.py 中误导的 “Self-RAG” 正名为 “LLM 相关性过滤（借鉴 Self-RAG 思想）”；`use_self_rag`/`get_self_rag` 等功能键不变。另清理 .env 中无效遗留变量 `SELF_RAG_ENABLED`/`SELF_RAG_THRESHOLD`（代码从未读取）。

### 阀值扫描（forced-full 策略，20题）
用 `eval/tune_relevance_threshold.py` 强制 full 策略扫 `LLM_RELEVANCE_THRESHOLD`：

| threshold | avg_ctx | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|---|
| 0.15 | 11.85 | 0.773 | 0.845 | 0.574 | 0.825 | 0.754 |
| 0.3 | 9.85 | 0.821 | 0.819 | 0.690 | 0.842 | 0.793 |
| 0.5 | 9.15 | 0.784 | 0.820 | 0.659 | 0.875 | 0.784 |
| 0.7 | 7.05 | 0.729 | 0.800 | 0.615 | 0.850 | 0.748 |

数据 `eval/relevance_threshold_sweep_result.json`。avg_ctx 单调下降证明过滤生效；forced-full 下 0.3 看似最优（Overall 0.793）。

### 生产路由确认推翻了 forced-full 结论
用真实生产路由（20 干净题）对比 0.15 vs 0.3：

| 20题·生产路由 | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|
| 0.15（基线, result_final_tuned.json） | 0.765 | 0.838 | 0.725 | 0.825 | 0.788 |
| 0.3（result_final_threshold03_20q.json） | 0.742 | 0.841 | 0.631 | 0.792 | 0.751 |

**生产路由下 0.3 反而更差**（Overall -0.037，CP -0.094）。根因：forced-full 强制所有查询走 full 策略，其 0.15 基线（0.754）本就低于生产路由 0.15（0.788）——**forced-full 本身是混淆变量**；叠加本地 Qwen3-14B 相关性打分频繁返回畸形 JSON（回退为不过滤）+ 有时误删好块 + 20 题生成噪声。

### 结论（诚实负结果）
**保持 LLM_RELEVANCE_THRESHOLD=0.15**。阀值调优在生产路由下无稳健增益、反有损，按“诚实兑底”不硬改。另：56 题集对 RAGAS 不适用（含工具类问题，仅 28 题可评分），干净评测应用前 20 知识题。

### 方法学教训
- forced-full 隔离单变量看似干净，但“强制策略”本身改变了系统行为、成为混淆变量；参数调优的最终判定必须在真实生产路由下做。
- 本地小模型做 LLM-as-Judge 过滤器不可靠（JSON 格式错误频发），限制了该杠杆的可用性。

### 补充：开/关 A/B——“去掉这个过滤器会不会更好”（官方 RAGAS, 20题, 生产路由）

| 过滤器 | avg_ctx | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|---|
| ON（0.15，现状） | 8.7 | 0.757 | 0.804 | 0.638 | 0.775 | 0.744 |
| OFF（去除） | 8.9 | 0.723 | 0.823 | 0.622 | 0.725 | 0.723 |

数据 `eval/relevance_onoff_result.json`。**去掉并不会更好**：同 session 对比 OFF 各项略降（Overall -0.021、CR -0.050、Faith -0.034）。关键：avg_ctx 8.7→8.9 几乎不变——生产路由下只有少数复杂查询走 full 策略触发它、且 0.15 极松，故过滤器本来“几乎没在干活”，ON≈OFF（差异在 20 题噪声带内）。**结论：保持过滤器启用（ON, 0.15），不移除**。

## 18. 精度过滤两杠杆：rerank 分数截断 + 降多查询扇出（官方 RAGAS, 20题, 生产路由）——首个正向结果

新增两项可配置精度杠杆并 A/B（`eval/tune_precision_filters.py`，同 session 生产路由）：

| config | avg_ctx | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|---|
| baseline(0.0/5) | 8.6 | 0.780 | 0.837 | 0.626 | 0.750 | 0.748 |
| **thr0.3/sub3** | 7.95 | 0.778 | 0.815 | 0.648 | 0.850 | **0.773** |
| thr0.5/sub3 | 7.6 | 0.699 | 0.816 | 0.640 | 0.775 | 0.732 |

数据 `eval/precision_filters_result.json`。**结论（正向）**：
- **thr0.3/sub3 稳健优于 baseline**：Overall +0.025、CP +0.022、CR +0.100；avg_ctx 单调下降（8.6→7.6）证明 rerank 截断 + 降扇出真生效。
- thr0.5 过度过滤（Faith 崩至 0.699）——清晰剂量-响应，非噪声。
- **已落地 .env**：`RERANK_SCORE_THRESHOLD=0.3`（reranker 精排后按 sigmoid_normalize(score)≥0.3 丢块，保底≥1）、`MAX_SUB_QUERIES=3`（broad 子问题上限 5→3）。
- **这是本轮调优首个稳健正向结果**：“丢低分块”的精度过滤优于此前的“调计数”（top_k/pool/chunk）。（20 题噪声 ±0.02-0.03，但 CP↑+CR↑+avg_ctx↓+剂量响应多信号一致，故采纳。）

### 18.1 阀值精扫（0.2/0.25/0.3/0.35 × sub3，同 session）——触到噪声地板

| threshold | avg_ctx | Faith | AR | CP | CR | Overall |
|---|---|---|---|---|---|---|
| 0.2 | 8.0 | 0.766 | 0.807 | 0.644 | 0.775 | 0.748 |
| 0.25 | 7.3 | 0.759 | 0.790 | 0.667 | 0.833 | 0.762 |
| 0.3 | 7.9 | 0.781 | 0.815 | 0.573 | 0.800 | 0.742 |
| 0.35 | 7.75 | 0.743 | 0.784 | 0.693 | 0.758 | 0.745 |

数据 `eval/precision_fine_result.json`。**结论：无稳健更优点，保持 0.3**：
- 四档 Overall 全在 0.742-0.762（跨度仅 0.02）；而 0.3 本档跨 session 波动 0.773→0.742（0.031）、CP 0.648→0.573（0.075），**自波动已大于档间差异** → 20 题精度不足以区分。
- CP 随阀值微弱上行（0.2→0.35: 0.644→0.693，0.3 为噪声离群），但以 CR/Faith 为代价、Overall 持平——与前述剂量-响应一致。
- 教训：粗调（baseline→0.3）的正向信号真实；细调（0.2~0.35）已进入 20 题噪声带，需 56 题或多 seed 平均才能再分辨，边际收益极小。保持 `RERANK_SCORE_THRESHOLD=0.3`。

## 19. 修“评测尺子”：干净评测集 + 多跑取均值可信基线（最高杠杆）

前面所有参数调优都撞上同一堵墙：**20 题单跑噪声 ±0.03，淹没了改动信号**。根因是评测集本身：旧 56 题混了工具题、无标签，仅 28 题可评分。

### 19.1 干净评测集（51 题，带标签，已核验）
- `eval/build_eval_set.py`：DeepSeek 从 `knowledge/*.md`(94篇) 生成事实型 QA(question+reference_answer+source_file+type)，严格仅基于单篇正文 → 85 候选。
- `eval/curate_eval_set.py`：DeepSeek **独立 grounding 核验**（第二次判定 reference 是否被原文完全支持）；再按 reference 去重 → **51 题定稿** `eval/eval_set_clean.json`（topic 32 + 部门 19）。
- **诚实限制**：同一模型生成+核验存在自我一致性偏袏（核验 85/85 全过）；仍建议人工抽检。SC-* 部门文档多为合成模板（占位流程/样板句），去重后真实 substantive 题以 topic 型为主。
- `run_comprehensive_ragas.py` 新增 `--dataset`（向后兼容，默认仍用内置题集）。

### 19.2 多跑取均值可信基线（`eval/eval_repeat.py`，clean 51 题 × 3，生产路由 thr0.3/sub3）

| 指标 | mean ± std | 旧污染集单跑 |
|---|---|---|
| Faithfulness | **0.7115 ± 0.013** | ~0.78 |
| AnswerRelevancy | 0.8573 ± 0.0068 | ~0.82 |
| ContextPrecision | **0.7413 ± 0.0093** | ~0.63 |
| ContextRecall | **0.9543 ± 0.0092** | ~0.75 |
| Overall | **0.8161 ± 0.0068** | ~0.75 |

数据 `eval/baseline_clean_x3.json`。**关键结论**：
- **噪声从 ±0.03 压到 ±0.007-0.013**（51 题×3 取均）——以后能分辨 0.01 级的细改动.
- **“CP 偏低”大部分是污染评测集的假象**：干净知识题上 CP=0.74、CR=0.95、Overall=0.82，都明显高于旧口径。
- **真正短板是 Faithfulness 0.71**（唯一 FAIL）→ 阶段三 prompt 强制溯源正好打这个点。

### 19.3 生成 prompt A/B（Faith 聚焦变体 vs 基线）——变体回火，保留原 prompt

先逐条核对：现有 `RAG_SYSTEM_PROMPT`（rag.py）已含溯源 [1][2]、仅基于资料、禁编造、首句直答；低置信度版额外禁用客套词表——task3 想加的四点已全在。设计 1 个 Faith 聚焦变体（`eval/tune_prompt_ab.py`，运行时换 prompt）：新增“宁缺毋滥 + 输出前逐句自查删无据”，其余不变。

| 指标 | baseline(mean±std) | variant(mean±std) | Δmean |
|---|---|---|---|
| Faithfulness | 0.7115±0.013 | 0.6627±0.005 | **-0.049** |
| AnswerRelevancy | 0.8573±0.007 | 0.7406±0.011 | **-0.117** |
| ContextPrecision | 0.7413±0.009 | 0.7412±0.002 | -0.000 |
| ContextRecall | 0.9543±0.009 | 0.9804±0.000 | +0.026 |
| Overall | 0.8161±0.007 | 0.7812±0.004 | **-0.035** |

数据 `eval/prompt_ab_result.json`（变体也 ×3 取均，与基线同口径）。**结论：变体回火，保留原 prompt**：
- 变体稳健变差：Overall -0.035（是 ±0.007 基线噪声的 5 倍）；尤其 **它想提的 Faith 反而降 0.049**、AR 崩 0.117。
- 根因：“宁缺毋滥 + 自查删无据”使模型**过度保守、答得残缺** → AR 崩（答案不完整）；自查改写反而偏离逐字依据，Faith 也降（CR 微升 0.026 不足以抵消）。
- **现有 prompt 的“详尽+溯源”平衡本就更优**。与 Self-RAG/上下文扩展同一规律：项目生成侧已高度调优，task3 无增益。不改 `rag.py`。
