# SmartQA 意图路由兜底策略 — OpenSpec 需求规格

> 版本: 1.0 | 作者: Hermes | 日期: 2026-05-13

---

## 1. 现状盘点

### 1.1 当前路由架构

```
用户输入 → 规则匹配（关键词/正则，<1ms）
              ↓ 未命中
         语义路由（embedding 相似度，<10ms）
              ↓ 未命中
         LLM 分类（~2-4s，最后兜底）
              ↓
         五种意图: GREETING | RAG_ANSWER | TOOL_CALL | HYBRID | UNCLEAR
```

### 1.2 当前 UNCLEAR 处理

```python
# chat.py
if intent == IntentType.GREETING:       # ← UNCLEAR 也走这里！
    answer = _handle_greeting(safe_query)  # 返回 "抱歉，我不太理解您的问题"
```

`_handle_greeting` 只有 3 条硬编码规则：

| 匹配 | 回复 |
|------|------|
| 你好/hi/hello | 你好！我是供应链助手 |
| 谢谢/感谢 | 不客气！ |
| 其他一切（含 UNCLEAR） | **抱歉，我不太理解您的问题，请提供更多细节。** |

### 1.3 问题严重性分析

| 问题 | 严重度 | 说明 |
|------|--------|------|
| UNCLEAR == GREETING 同路径 | 🔴 P0 | 两个不同概念被合并——"你好"和"我不知道你在问什么"不是一回事 |
| 所有未知 query 返回同一句客套话 | 🔴 P0 | 用户问任何系统不理解的问题都得到同一句回复，体验极差 |
| GREETING 覆盖范围过宽 | 🟡 P1 | `_handle_greeting` 不应该作为兜底，兜底应该是 RAG |
| 无追问机制 | 🟡 P1 | 意图不明时不应该直接放弃，应该主动缩小范围 |
| clarify.py 已实现但未接入 UNCLEAR | 🟡 P1 | `check_needs_clarification` 只检查工具参数缺失，不处理意图不明 |

### 1.4 真实影响

用户实测反馈：
- "这是啥" + 图片 → VLM 描述了图片 → 但路由判 UNCLEAR → 客套话
- 我改了两处（有图片时强制 RAG + 路由用增强 query）缓解了图片场景，但**纯文本场景依然受影响**

---

## 2. 需求分析

### REQ-1: UNCLEAR 独立于 GREETING [P0]

**描述:** UNCLEAR 和 GREETING 是两个不同语义——GREETING 是用户主动问候，UNCLEAR 是系统能力不足。分开处理。

**当前:**
```python
# router.py IntentType enum
GREETING = "greeting"
UNCLEAR = "unclear"

# chat.py — 两个 intent 走同一分支
if intent == IntentType.GREETING:
    answer = _handle_greeting(safe_query)  # UNCLEAR 也进这里
```

**期望:**
```python
if intent == IntentType.GREETING:
    # 真正的问候：你好/谢谢/再见 → 友好回复
    answer = _handle_greeting(safe_query)

elif intent == IntentType.UNCLEAR:
    # 意图不明 → 不扔客套话，而是：
    # 1. 尝试 RAG 检索兜底（用 query 搜知识库）
    # 2. 搜到东西？展示结果 + 追问
    # 3. 搜不到？坦诚告知 + 追问 + 给建议
```

**验收标准:**
- [ ] GREETING 和 UNCLEAR 在 `chat.py` 中是两个独立分支
- [ ] GREETING 只匹配明确的问候模式（你好/谢谢/再见/早上好等）
- [ ] UNCLEAR 触发 RAG 检索兜底，不直接放弃

**面试价值:**
「意图路由有五种结果。GREETING 是用户主动问候，UNCLEAR 是系统能力不足——两者处理策略完全不同。UNCLEAR 不会直接回复"我不懂"，而是用 RAG 检索做最后一次尝试，搜到就展示，搜不到就坦诚告知并引导用户。」

---

### REQ-2: RAG 检索兜底 [P0]

**描述:** UNCLEAR 时不做空回复，而是把用户 query 扔进 RAG 检索，看知识库有没有相关内容。

**为什么这是 P0:** 这是解决「除了既定问题全部返回客套话」的核心手段。即使路由器没匹配到意图，知识库可能有相关内容。

**验收标准:**
- [ ] UNCLEAR 分支调用 `rag_engine.search(query, top_k=3)`
- [ ] 检索到结果 → 正常展示 + 提示"根据你的问题，找到以下相关内容"
- [ ] 检索为空 → 展示追问："你是指以下哪个方面？" + 列出知识库的文档标题供选择
- [ ] SSE 事件流完整（route → query_analysis → dag_progress 等）

**面试价值:**
「UNCLEAR 不是系统的终点，而是 RAG 检索的起点。即使意图路由失败了，我们还有一层兜底——直接把用户 query 扔进知识库检索。因为用户问的问题，只要知识库有相关内容，就不应该返回 '我不懂'。」

---

### REQ-3: 改进 GREETING 覆盖范围 [P1]

**描述:** 当前 `_handle_greeting` 太简陋，而且覆盖了一些不应该进 GREETING 的 query。需要改进规则匹配，让真正的 GREETING 更精确。

**当前规则匹配的 GREETING 关键词:**
```
你好/嗨/hi/hello/hey/在吗/在不在
谢谢/感谢/thanks/thank you
早上好/晚上好/下午好
再见/拜拜/bye
你是谁/你叫什么/你能做什么
```

**问题:** 这些规则太短，容易误匹配。比如"你好，我想问库存"→ 规则匹配 GREETING → 但实际意图是 TOOL_CALL。

**验收标准:**
- [ ] 规则匹配检查 query 长度：如果 query > 10 字且包含关键词，不匹配 GREETING（继续走后续路由）
- [ ] GREETING 回复增加引导性问题："你好！我是供应链助手，可以帮你查库存、查订单、检索制度文档。有什么需要？"

---

### REQ-4: UNCLEAR 追问引导 [P2]

**描述:** UNCLEAR 且 RAG 无结果时，不返回空洞的"我不懂"，而是列出系统能力和建议。

**验收标准:**
- [ ] 列出当前知识库的文档标题（前 5 个）
- [ ] 列出可用工具（按当前用户角色过滤）
- [ ] 建议提问方式：「试试问我 "新供应商准入需要什么资质" 或 "查一下 MAT-001 的库存"」

---

## 3. 优先级总结

```
P0: REQ-1 (UNCLEAR 独立分支) + REQ-2 (RAG 检索兜底)  ← 解决核心问题
P1: REQ-3 (GREETING 精确化)
P2: REQ-4 (追问引导)
```

---

## 4. 不做的事

- ❌ 不引入新的 LLM 调用（UNCLEAR 已经在 LLM 分类阶段消耗了 token）
- ❌ 不改变路由器的三层架构（规则→语义→LLM）
- ❌ 不修改 GREETING 和 UNCLEAR 的语义路由 embedding（那是独立优化项）
