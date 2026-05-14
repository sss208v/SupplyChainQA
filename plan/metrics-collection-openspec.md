# Agent 工具调用指标 + 成果量化 — OpenSpec 需求规格

> 版本: 1.0 | 日期: 2026-05-14 | 参考: 牛客网 Agent 简历攻略

---

## 1. 目标

让 SmartQA 的简历描述达到攻略标准：面试官在 3 秒内看到候选人在 Agent 架构中的核心贡献。当前缺失两项：

### 缺失 1：工具调用指标（Monitoring & Feedback）
攻略要求：**"采集各类工具调用指标（次数、耗时、成功率），用于 A/B 测试与性能优化"**

当前代码：`chat.py` 的 SSE 事件中有 `performance_metrics`（总路由/检索/LLM 耗时），但**没有 per-tool 维度的指标**。

### 缺失 2：量化成果
攻略要求：**"XX 任务完成率从 65% 提升至 92%"、"工具调用平均耗时缩短至 1.2s"**

当前代码：没有 A/B 对比数据。

---

## 2. 需求清单

### REQ-1：工具调用指标收集器 [P0]

**描述：** 新增 `app/core/tool_metrics.py`，记录每次工具调用的 name / input / output / duration_ms / success / timestamp，提供查询接口。

**验收标准：**
- [ ] 每次 `ToolNode` 执行后自动记录指标
- [ ] 记录字段：tool_name, input_preview(100chars), output_preview(100chars), duration_ms, success(bool), timestamp
- [ ] 存储到 SQLite（与工具数据同一个 DB）或 Redis
- [ ] 提供查询接口：`GET /api/v1/metrics/tools?limit=50`（admin only）
- [ ] 返回 per-tool 统计：调用次数、平均耗时、成功率

**面试价值：** "采集工具调用全链路指标，支持 A/B 测试与性能优化"

### REQ-2：成果数据落地 [P0]

**描述：** 系统已有实际可测的数字，需要整理为简历可用的量化成果。

**已有数据：**
| 指标 | 数值 | 来源 |
|------|------|------|
| 跨部门查询关键词覆盖率 | 100%（10/10） | 实测 |
| Reranker 开启后 CP 提升 | 0.53→0.67（+26%） | RAGAS |
| 知识库大小 | 2425 chunks / 92 篇 | Milvus |
| 权限粒度 | 7 部门，finance 仅 2 工具 | 实测 |
| 规则路由覆盖率 | 90% 请求不调 LLM | 架构设计 |

**缺失：** 缺少 A/B 对照组（如"无 Agent vs 有 Agent"的对比）。

**解决方案：** 用工具本身的数据做"工具调用成功率"的统计，作为可展示的数字。同时设计一个简单的对比场景。

**验收标准：**
- [ ] 生成一份工具调用统计报告（调用工具脚本批量跑 20 次查询，统计成功率）
- [ ] 至少产出 2 个可写入简历的对比数字

### REQ-3：指标可视化（前端） [P2]

**描述：** 前端新增一个简单的指标仪表盘（可在 Evaluate 页面扩展）。

**验收标准：**
- [ ] Evaluate 页面新增"工具调用统计"卡片
- [ ] 显示 per-tool 调用次数和成功率

---

## 3. 实施计划

### Step 1：工具指标收集器（30 行）
```python
# app/core/tool_metrics.py
class ToolMetrics:
    def record(tool_name, input, output, duration_ms, success): ...
    def stats() -> dict: ...  # per-tool 统计
    def recent(limit=50) -> list: ...
```

### Step 2：接入 ToolNode
在 `tool.py` 的 agent 执行后，从 `tool_calls_record` 提取指标写入。

### Step 3：生成对比数据
写一个评测脚本跑 20 次查询，统计工具调用成功率。与"不使用 Agent 直接调工具"做对比。

### Step 4：更新面试 HTML
把量化数字写入简历模板。

---

## 4. 验收检查表

```
[ ] REQ-1: tool_metrics.py 实现，ToolNode 集成
[ ] REQ-1: GET /metrics/tools 端点可用
[ ] REQ-2: 工具调用成功率报告产出（≥20 次查询）
[ ] REQ-2: 至少 2 个量化对比数字可写入简历
[ ] REQ-3: 前端指标卡片（可选）
```
