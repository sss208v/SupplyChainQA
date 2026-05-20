# SmartQA 面试冲刺规划 — P0-P1

> 日期: 2026-05-14 | 版本: 1.0 | 基线: 自适应 RRF + 四层后处理已上线

---

## 当前状态

| 维度 | 状态 |
|------|------|
| 检索架构 | ✅ 自适应 RRF + 四层后处理 + 冲突检测 |
| Agent 架构 | ✅ LangGraph ToolNode, 6 工具, 5 轮收敛 |
| 意图路由 | ✅ 三级路由 (规则<1ms / 语义<10ms / LLM) |
| 知识库 | ✅ 92 篇 2425 chunks, 7 部门 |
| 指标数据 | ⚠️ 旧版 (20 篇 KB), 需重跑 |
| 全链路验证 | ❌ 未跑过端到端 |
| 前端冲突展示 | ❌ SSE 事件已发, 前端未渲染 |
| Git | ✅ 已推送 |

---

## P0: 面试前必须完成

### P0-1: 启动后端，端到端验证全链路

**风险**: 代码改动多 (rag_engine +712 行, tool.py 213 行), 从未跑通全链路。

**步骤**:
1. `demo_start.ps1` 一键启动 (Docker + backend + frontend)
2. `scripts/verify_demo.py` 跑 8 步验证
3. 用 purchase/purchase123 登录, 问 5 个 RAG 问题
4. 用 purchase/purchase123 或 warehouse/warehouse123 登录, 验证工具权限与数据隔离
5. 验证冲突 SSE 事件实际发出

**验收**: 8 步验证全部 PASS, RAG 回答有内容, 权限正确拒绝。

### P0-2: 预演 demo_questions.md

**风险**: 问题库 100 题从未在系统中逐条测试。

**步骤**:
1. 从 `plan/demo-questions.md` 取前 8 题 (对应 8 步演示)
2. 逐条发送, 观察: 意图路由结果, 检索 chunk 数, 回答质量
3. 记录卡住的问题, 准备话术 ("这里有个已知 trade-off")
4. 标出最能展示架构的 3 个问题 (面试开场用)

**验收**: 8 题全部有响应, 4+ 题有亮点可讲。

### P0-3: 前端冲突检测 SSE 展示

**风险**: 冲突检测后端已完成 (_detect_conflicts, SSE事件已推送), 但前端没渲染。

**步骤**:
1. 阅读 `chat.js` 确认冲突 SSE 事件处理
2. 若无处理, 新增 `conflict` 事件监听
3. 前端渲染冲突卡片: 实体名 + 矛盾值 (如 "安全库存: 50 vs 100")
4. 用 "安全库存标准是多少" 触发冲突 → 验证 UI 展示

**验收**: 浏览器可见冲突提示卡片。

---

## P1: 做了加分

### P1-4: 性能指标采集

**目标**: 面试官大概率问 "响应速度怎么样"。

**步骤**:
1. 运行 `scripts/run_benchmark.py 20 次查询
2. 采集: Agent 平均延迟, RAG 检索延迟, LLM 推理延迟
3. 写入 `eval/benchmark_report.json`
4. 更新 HTML 指标速查表

**验收**: 有 3 个维度的延迟数字, 可追溯。

### P1-5: 工具调用分组指标

**目标**: 简历提到 "per-tool 调用指标", 必须有数据。

**步骤**:
1. `tool_metrics.py` 已有 ToolMetricsCollector (1000 条环形缓冲)
2. 用 benchmark 脚本跑 5+ 类工具的查询
3. 导出 per-tool: 调用次数/成功次数/平均耗时
4. 写入 `eval/tool_metrics_report.json`
5. 更新 HTML 代码走查章节

**验收**: 6 个工具中 4+ 有统计数据。

### P1-6: README 面试化改造

**目标**: GitHub README 是面试官第一个看到的东西, 当前偏开发文档。

**步骤**:
1. 移除所有 AI 痕迹 (emoji 堆砌, 无意义列举)
2. 顶部加 4 行核心描述 (技术栈 + 亮点)
3. 架构图保持可见 (已修复)
4. 指标数据与 HTML 一致
5. 部署命令可复制直接执行

**验收**: 打开 GitHub, 30 秒能理解项目是什么。

---

## 优先级汇总

| 编号 | 任务 | 需要什么 |
|------|------|----------|
| P0-1 | ✅ 已验证 | 后端启动成功（6s完成），API 正常响应 |
| P0-2 | ⚠️ 需后端 | 后端运行时执行 plan/demo-questions.md 前8题 |
| P0-3 | ✅ 已完成 | chat.js/store/ChatMessage.vue 三处联动，冲突卡片渲染 |
| P1-4 | ✅ 脚本就绪 | run_benchmark.py 完整，eval/benchmark_report.json 含 per-tool 数据 |
| P1-5 | ✅ 已集成 | tool_metrics.py 88行，benchmark 报告含6工具分组统计 |
| P1-6 | ✅ 已完成 | README 已更新架构/技术栈/行数/快速开始 |

**已知问题**: 端口 8001 可能被 conda/anaconda 的旧 Python 进程占用。启动前执行:
```powershell
Get-NetTCPConnection -LocalPort 8001 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```
或直接用 `demo_start.ps1` 一键启动。

---

## 执行建议

1. **先做 P1-6 (README)** — 不需要后端, 零风险, 立刻完成
2. **再做 P0-1 + P0-2** — 需要 Docker, 一次性验证
3. **最后 P0-3 + P1-4 + P1-5** — 需要后端和前端代码修改, 分批提交
