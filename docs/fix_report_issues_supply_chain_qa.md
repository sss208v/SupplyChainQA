# Supply Chain QA 审查问题修复报告

> 对应文档：`issues_supply_chain_qa.md`（2026-08-02 项目审查报告）
> 修复时间：2026-08-02
> 修复方式：先测试复现确认 → 修复 → 回归验证

## 结论总览

对审查报告 5 项指控逐项做**静态代码检查 + mock 行为测试**实测后：

| #   | 指控                                   | 实测判定             | 处置                      |
| --- | -------------------------------------- | -------------------- | ------------------------- |
| 2   | "三级级联意图路由"只有一级             | 误判                 | 不改动（真实实现存在）    |
| 3   | "基于 LangGraph ReAct"实为手写顺序执行 | 误判                 | 不改动（真实实现存在）    |
| 4   | "三层缓存"只有两层                     | 误判                 | 不改动（L1/L2/L3 均存在） |
| 5   | orchestrator 异常处理有 NameError      | **属实（触发面窄）** | **已修复**                |
| 6   | BM25 分词无停用词过滤                  | **属实**             | **已修复**                |

修复范围：2 项（#5、#6）；新增回归测试 8 个；全量单元测试 1165 通过，无回归。

---

## Issue #5: orchestrator 异常处理 NameError（已修复）

### 问题确认（测试复现）

- 场景 A：mock `llm.ainvoke()` 抛 `json.JSONDecodeError` → 修复前实测抛
  `UnboundLocalError: cannot access local variable 'content' where it is not associated with a value`
- 场景 B：mock `llm.ainvoke()` 抛一般异常（`RuntimeError`）→ 走 `except Exception` 分支，不触发
- 判定：隐患真实存在，但仅当 `ainvoke` 抛 `json.JSONDecodeError`（LLM 客户端响应解析失败）时触发，
  报告将其描述为"ainvoke 失败就抛 NameError"过于宽泛

### 根因

`plan()` 中 `content = response.content.strip()` 在 `try` 块内定义，外层
`except json.JSONDecodeError` 分支引用 `content[:200]`。当异常发生在 `ainvoke()` 阶段时，
`content` 从未赋值，引用即抛 `UnboundLocalError`（Python 3.11 中 `NameError` 的局部变量形态）。

### 修复内容

`backend/app/agents/orchestrator.py` — `plan()`：

```python
# content 在 try 外初始化：若 ainvoke 阶段抛异常，外层 except 分支
# 引用 content 时不会因未定义而抛 NameError/UnboundLocalError
content = ""
try:
    response = await llm.ainvoke(messages)
    content = response.content.strip()
```

改动最小化：仅提前初始化局部变量，不改变任何异常处理分支的返回语义。

### 验证

新增 `backend/tests/test_orchestrator.py`（4 个用例）：

| 用例                                                  | 验证点                                                                          |
| ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| `test_plan_ainvoke_json_decode_error_no_unboundlocal` | ainvoke 抛 JSONDecodeError 不再抛 UnboundLocalError，返回 error dict（回归 #5） |
| `test_plan_ainvoke_generic_error_graceful`            | 一般异常返回 error dict，不向上抛                                               |
| `test_plan_success_returns_plan`                      | 正常路径仍返回 plan                                                             |
| `test_plan_parse_error_graceful`                      | 非 JSON 内容走内层 except，返回 error dict                                      |

修复前：该用例失败（复现 UnboundLocalError）；修复后：通过。

---

## Issue #6: BM25 分词无停用词过滤（已修复）

### 问题确认（测试复现）

- 静态：`_tokenize()` 源码无任何停用词表/过滤逻辑
- 行为（注入 fake jieba 模拟 jieba 路径）：`"供应商的库存是多少"` → `['供应商', '的', '库存', '是', '多少']`，
  "的""是"未被过滤，直接进入 BM25 语料
- 影响：高频虚词（的/了/是/在/有）在所有文档中普遍出现，稀释 IDF 区分度，降低 BM25 召回质量

### 修复内容

`backend/app/core/rag/bm25.py`：

1. 模块级新增 `_STOP_WORDS` 停用词表（中文高频虚词/助词 39 个 + 英文高频功能词 26 个）
2. `_tokenize()` 返回前过滤：

```python
tokens = en_words + cn_chars + numbers
# 过滤停用词（中文虚词直接比较；英文按小写比较，兼顾 The/IS 等大小写变体；
# 数字 token 不受影响，保留 MAT-001 等编码数字）
return [t for t in tokens if not (t.isalpha() and t.lower() in _STOP_WORDS)]
```

设计约束：

- **数字不误伤**：`MAT-001` 的 `001` 是 `isalpha()=False`，保留
- **大小写语义保持**：英文实义词保留原始大小写（BM25 大小写敏感的原注释不变），仅按小写匹配停用词
- **fallback bigram 兼容**：字符 bigram 路径的变形 token（如"商的"）不在停用词表内，行为不变
- **空输入兼容**：空字符串仍返回 `[]`（既有测试 `test_tokenize_empty` 不受影响）

### 验证

新增 `backend/tests/test_bm25.py` 的 `TestBM25StopwordFilter`（4 个用例）：

| 用例                                             | 验证点                                    |
| ------------------------------------------------ | ----------------------------------------- |
| `test_tokenize_filters_cn_stopwords`             | 中文停用词过滤，业务词（供应商/库存）保留 |
| `test_tokenize_filters_en_stopwords`             | 英文停用词（the/is）过滤，实义词保留      |
| `test_tokenize_stopword_only_text_returns_empty` | 纯停用词文本 → 空列表                     |
| `test_tokenize_empty_still_empty`                | 空输入仍返回 `[]`（兼容既有行为）         |

修复前：4 个用例失败；修复后：通过。

---

## 误判项说明（未改动）

### Issue #2 — "三级级联意图路由"只有一级（误判）

报告引用 `rag.py::_classify_query()`，但该方法只是 **query 类型分类**（specific/ambiguous/broad），
不是意图路由。真实的三级意图路由在 `app/agents/router.py::RouterAgent.route()`：

1. **规则层** `_rule_match()`：实体编码/命令词/目标词/问句正则，确定性短路（实测 `MAT-001 还剩多少库存` → tool_call）
2. **语义层** `semantic_router.route()`：embedding 余弦相似度 + 阈值/margin 双门槛（实测命中返回 method=semantic）
3. **LLM 层** `_llm_classify()`：语义未命中时回退 LLM 分类（实测触发 method=llm）

语义样本外置于 `app/data/intent_routes.json`（mtime 热加载），符合 AGENTS.md"改配置不改代码"约束。

### Issue #3 — "基于 LangGraph ReAct"实为手写顺序执行（误判）

`app/agents/base_agent.py` 使用 `StateGraph(AgentState)` 构建 `agent_node → tools_node → agent_node`
ReAct 循环，含条件边 `route_after_agent` 与 `MAX_ITERATIONS=5` 停止条件；
实测构建出 `langgraph.graph.state.CompiledStateGraph`。`ToolAgent`/`DomainAgent` 均继承 `BaseReActAgent`。
报告只检查了 `orchestrator.py`（goal 意图的跨域编排器，手写 plan→execute→summarize 属实），
未检查业务 Agent 链路的真实实现。

### Issue #4 — "三层缓存"只有两层（误判）

`app/core/cache_manager.py` 为 4 层缓存门面：L1 进程内 LRU / L2 Redis 语义缓存 / L3 Redis 查询结果
read-through / L4 nginx 静态资源。L3 被 `tool_engine.py`、`text_to_sql.py` 实际调用；
版本号失效机制存在：`semantic_cache.invalidate()` 实测执行 `incr("scqa:kb:version")`（O(1) 失效）。
报告只检查了 `engine.py`（RAG 检索链路仅使用 L1/L2），未检查缓存门面实现。

---

## 回归验证

| 验证范围                                            | 结果                                                |
| --------------------------------------------------- | --------------------------------------------------- |
| 新增测试（test_orchestrator.py + test_bm25.py）     | 17 passed（含修复前失败的 4 个用例）                |
| 全量单元测试 `pytest tests -q -k "not integration"` | **1165 passed, 2 skipped, 46 deselected**，0 failed |

无回归；预提交钩子（pre-commit）三件套可在提交时自动复跑。

---

## 涉及文件

| 文件                                 | 变更                                    |
| ------------------------------------ | --------------------------------------- |
| `backend/app/agents/orchestrator.py` | 修复 #5：`content` 提前初始化           |
| `backend/app/core/rag/bm25.py`       | 修复 #6：新增 `_STOP_WORDS` + 分词过滤  |
| `backend/tests/test_orchestrator.py` | 新增：plan() 异常路径回归测试（4 用例） |
| `backend/tests/test_bm25.py`         | 追加：停用词过滤测试（4 用例）          |
