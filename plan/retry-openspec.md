# SmartQA Retry 机制增强 — OpenSpec 需求规格

> 版本: 1.0 | 作者: Hermes | 日期: 2026-05-12

---

## 1. 目标 (Goal)

将现有的 retry 机制从「仅覆盖非流式 LLM 调用」扩展到覆盖**流式 LLM 调用（主路径）**和**向量检索**两个关键链路，确保面试演示时不会因为临时网络抖动而中断服务。

**一句话：** 让 SmartQA 的容错能力从"有但不够"变成"面试官问到能自信回答"。

---

## 2. 当前状态盘点

### 2.1 已有能力

| 组件 | 文件 | 能力 |
|------|------|------|
| retry 装饰器 | `core/retry.py` | `retry_async`（异步指数退避 2s→4s→8s，3次）、`retry_sync`（同步版） |
| LLM 非流式调用 | `core/llm_router.py:147` | `LLMFactory.ainvoke` 加了 `@retry_async` ✅ |

### 2.2 缺失覆盖

| 场景 | 调用位置 | 失败后果 | 面试风险 |
|------|----------|----------|----------|
| **流式 LLM 调用** | `api/chat.py:532` `LLMFactory.astream()` | SSE 连接直接断开，用户看到空白 | 🔴 最高——这是主路径 |
| **Milvus 检索** | `core/rag_engine.py:467` `self.collection.search()` | 单次查询失败返回空结果 | 🟡 中——用户看到"未找到" |
| **Milvus 连接** | `core/milvus_client.py:30` | 整个 RAG 功能不可用 | 🟡 中——启动时已处理 |
| **Redis 操作** | `core/redis_client.py` | 对话记忆失败，Query Cache 失效 | 🟢 低——非致命降级 |
| **Web 搜索兜底** | `core/confidence_router.py` | 低置信度时没有外部信息补充 | 🟢 低——兜底路径，本身就有降级 |

---

## 3. 需求清单 (Requirements)

### REQ-1: 流式 LLM 调用 retry [P0]

**描述:** 给 `LLMFactory.astream` 加指数退避重试。

**为什么这是 P0:** `chat.py:532` 走的 `astream` 是用户发消息的实际路径。当前如果 DeepSeek API 在流式传输中途断开（比如网络抖动、API 过载），SSE 连接直接中断，用户看到空白。`ainvoke` 有 retry 但 `astream` 没有——主路径反而没保护。

**技术难点:** 流式调用不能简单用装饰器包裹，因为：
1. `astream` 是 async generator，装饰器包裹 generator 需要特殊处理
2. 流中途断开时，已发出的 chunk 不能撤回，重试需要从头开始生成
3. 需要区分"连接失败"（可重试）和"内容生成中失败"（不可重试，因为用户已经看到部分内容）

**验收标准:**
- [ ] `LLMFactory.astream` 在抛出 `APIConnectionError` / `APITimeoutError` 时自动重试（最多 3 次）
- [ ] 重试在**第一个 chunk 发出之前**生效——如果已经有内容流出了，不重试（避免重复输出）
- [ ] 重试失败 3 次后，yield 一个 SSE error 事件告诉前端，不静默断开
- [ ] 日志记录每次重试的尝试次数和失败原因

**面试价值:** 「流式调用也有 retry——在第一个 token 到达之前如果连接失败会自动重试，3 次指数退避。但如果已经有内容流出了就不重试，因为用户已经看到了部分结果，重复输出更糟糕。」

---

### REQ-2: Milvus 检索 retry [P1]

**描述:** 给 `rag_engine.search()` 加 retry。

**为什么是 P1:** Milvus 检索偶尔会因为网络抖动失败（gRPC 超时），当前直接返回空结果，用户看到"未找到相关信息"。实际上重试一次往往就能成功。

**验收标准:**
- [ ] `RAGEngine.search()` 在 gRPC 超时/连接错误时自动重试（最多 2 次，间隔 1s）
- [ ] 重试失败后返回空结果 + warning 日志（不抛异常，保证服务不中断）
- [ ] 向量检索和 BM25 检索分别独立重试

**面试价值:** 「检索层有轻量重试——gRPC 超时后自动重试 2 次，因为向量数据库的网络抖动很常见，重试一次成功率就能从 95% 提到 99.5%。」

---

### REQ-3: 前端错误提示 [P2]

**描述:** 当后端 retry 全部失败时，前端显示有意义的错误消息而非空白。

**当前状态:** chat.py 没有对 `astream` 的异常处理，错误可能被 FastAPI 默认 handler 吞掉，前端收不到 SSE error 事件。

**验收标准:**
- [ ] chat.py 的 `event_generator` 中 `LLMFactory.astream` 外层加 try/except
- [ ] 异常时 yield SSE `error` 事件：`{"type": "error", "message": "服务暂时不可用，请稍后重试"}`
- [ ] 前端 `chat.js` 的 SSE parser 在收到 error 事件时显示 toast 提示

---

### REQ-4: 面试话术更新 [P2]

**描述:** 更新 `docs/interview-showcase.html` 中"人工介入机制"部分，加入 retry 的具体数据。

**验收标准:**
- [ ] 在 showcase HTML 中新增 retry 相关描述：覆盖范围、退避策略、面试话术

---

## 4. 非需求 (Non-Requirements)

以下明确**不在本规格范围内**：

- ❌ Redis 操作 retry（连接池自带重连，不需要额外处理）
- ❌ 全链路分布式 tracing（OpenTelemetry 等，过度设计）
- ❌ 熔断器（Circuit Breaker）——当前规模不需要，面试 demo 不会打到触发熔断的 QPS
- ❌ Token 消耗统计（已在 REQ-1 中，不重复）
- ❌ 流式内容去重（重试时已发出的 chunk 怎么处理）——通过"第一个 chunk 前才重试"规避了这个问题

---

## 5. 实施任务（待 Phase 2 分解）

| # | 任务 | 优先级 | 估时 | 涉及文件 |
|---|------|--------|------|----------|
| 1 | 给 `astream` 加 pre-first-chunk retry 包装器 | P0 | 20min | `core/llm_router.py` |
| 2 | 给 `RAGEngine.search()` 加 retry | P1 | 10min | `core/rag_engine.py` |
| 3 | chat.py 加 astream 异常捕获 + SSE error 事件 | P2 | 10min | `api/chat.py` |
| 4 | 前端 SSE error 事件处理 | P2 | 10min | `api/chat.js`, `stores/chat.js` |
| 5 | 验证端到端（模拟 API 失败 → 重试 → 成功） | — | 15min | 测试脚本 |
| 6 | 更新 interview-showcase.html retry 话术 | P2 | 5min | `docs/interview-showcase.html` |

---

## 6. 验收检查表

```
[ ] REQ-1: astream 在无内容输出时自动重试（最多3次，指数退避）
[ ] REQ-1: 已有内容输出后不重试
[ ] REQ-1: 3次全部失败后 yield SSE error 事件
[ ] REQ-2: Milvus search() 在 gRPC 超时时自动重试（最多2次）
[ ] REQ-2: Milvus 全部失败后返回空结果不抛异常
[ ] REQ-3: 前端收到 SSE error 事件时显示 toast
[ ] REQ-4: interview-showcase 更新 retry 话术
[ ] 验证测试通过（模拟失败场景）
```
