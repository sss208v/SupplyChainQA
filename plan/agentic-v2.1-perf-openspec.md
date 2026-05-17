# SmartQA Agentic v2.1 — 性能与完善 OpenSpec

> 版本: 1.0 | 日期: 2026-05-18 | 依赖: v2.0 Agentic 升级已完成

---

## 1. 当前问题清单

| # | 问题 | 严重度 | 根因 |
|---|------|--------|------|
| 1 | Orchestrator 延迟 45s | P0 | 5 次串行 LLM 调用（plan + 3 agent + summarize），单次 ~5s |
| 2 | DeepSeek 模型未升级到 V4 | P1 | 当前用 `deepseek-chat`（V3），V4 Flash 快 3-5x |
| 3 | chat_completions GOAL 无 user_id | P1 | `user_id=None`，聊天记忆不隔离 |
| 4 | verify_demo.py 无 GOAL 场景 | P2 | 28 项全是 v1.0 单 Agent 测试 |
| 5 | 未提交 Git | P0 | 所有改动在工作区 |

---

## 2. 需求

### REQ-1: 接入 DeepSeek V4 Flash [P0]

**描述**: 配置 `deepseek-v4-flash` 作为快速模型，用于编排层（plan/summarize）。Agent 工具调用继续用 `deepseek-chat`（V3，稳定）。

**方案**:
```
config.py:
  DEEPSEEK_MODEL = "deepseek-chat"       # 主模型（工具调用）
  DEEPSEEK_FAST_MODEL = "deepseek-v4-flash"  # 快速模型（编排/分类）

llm_router.py:
  LLMFactory.get_llm(model="fast") → V4 Flash (temp=0)
  LLMFactory.get_llm(model="main") → V3 Chat (temp=0)

orchestrator.py:
  plan()       → LLMFactory.get_llm(model="fast")
  summarize()  → LLMFactory.get_llm(model="fast")
  domain agents → LLMFactory.get_llm(model="main")  # 不变
```

**验收**:
- [ ] `.env` / `config.py` 支持 `DEEPSEEK_FAST_MODEL`
- [ ] LLMFactory.get_llm() 支持 model 参数选择
- [ ] Orchestrator plan/summarize 用 fast model
- [ ] 延迟从 45s → 预期 15-20s（LLM 时间缩短 3x + 减少调用次数）

### REQ-2: Orchestrator 减少 LLM 调用 [P0]

**描述**: 合并 plan+summarize，减少串行 LLM 调用次数。

**当前调用链**:
```
plan (LLM #1) → agent1 (LLM #2) → agent2 (LLM #3) → agent3 (LLM #4) → summarize (LLM #5)
= 5 次 LLM 调用
```

**优化后**:
```
plan (LLM #1, fast) → agent1 (LLM #2) → agent2 (LLM #3) → agent3 (LLM #4) → 直接拼接
= 4 次 LLM 调用，最后一步用模板拼接而非 LLM 汇总
```

**进一步优化（可选）**: 2 步以内的简单编排，直接用单次 LLM 调用完成（输出计划 + 最终回答）

**验收**:
- [ ] 3 步以内场景：summarize 改为模板拼接（不调 LLM）
- [ ] 4 步以上场景：保留 LLM summarize
- [ ] 延迟 < 25s（fast model + 减少调用）

### REQ-3: chat_completions GOAL 补全 user_id [P1]

**修复**: 在 chat_completions 中提取 user，传入 orchestrator。

### REQ-4: verify_demo.py 增加 GOAL 场景 [P1]

**新增测试**:
- GOAL-1: "帮我评估 MAT-001 库存风险" → 期望返回含跨域分析的回答
- GOAL-2: 确认 SSE 事件包含 orchestrator_plan / agent_step
- GOAL-3: goal 意图不影响现有 TOOL_CALL 路径

### REQ-5: Git 提交 [P0]

压缩为一个 commit，message 简短真人风格。

---

## 3. 实施任务

### Step 1: LLMFactory 支持双模型（30 分钟）
- config.py: 加 `DEEPSEEK_FAST_MODEL`
- llm_router.py: `get_llm()` 加 `model` 参数
- MODEL_PRICING 加 V4 Flash 定价

### Step 2: Orchestrator 优化（30 分钟）
- plan() 用 fast model
- summarize() 少于 4 步直接拼接
- summarize() 4+ 步用 fast model

### Step 3: chat_completions 修复（5 分钟）
- GOAL 处理中提取 user_id

### Step 4: verify_demo.py 更新（20 分钟）
- 加 3 个 GOAL 场景测试

### Step 5: 端到端验证 + Git 提交（15 分钟）

---

## 4. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `config.py` | 改 | +DEEPSEEK_FAST_MODEL |
| `llm_router.py` | 改 | get_llm() +model 参数 |
| `orchestrator.py` | 改 | fast model + 模板拼接 |
| `chat.py` | 改 | GOAL user_id 提取 |
| `verify_demo.py` | 改 | +3 GOAL 场景 |
| `.env.example` | 改 | +DEEPSEEK_FAST_MODEL |

---

## 5. 验收

```
[ ] config.py 编译通过，两个 model 字段存在
[ ] LLMFactory.get_llm(model="fast") 返回 V4 Flash 实例
[ ] Orchestrator.run() 延迟 < 25s（此前 45s）
[ ] chat_completions GOAL 携带 user_id
[ ] verify_demo.py 28 + 3 = 31 项（GOAL 需要后端在线）
[ ] git log 干净，1 commit
```
