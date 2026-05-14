# Agent 架构升级 — LangChain + LangGraph 统一 Agent

> 版本: 1.0 | 日期: 2026-05-14

---

## 1. 现状分析

### 当前：三种独立 Agent，一行配置切换

```
AGENT_TYPE=react      → tools/agents/tool.py        (手写 ReAct, 291行)
AGENT_TYPE=langchain  → tools/agents/langchain_agent.py (AgentExecutor)
AGENT_TYPE=langgraph  → tools/agents/langgraph_agent.py (StateGraph)
```

### 问题

- **面试价值低**：三个方案互不关联，"我试了三个框架"<"我知道什么时候用哪个"
- **架构不实际**：生产系统不会维护三套 Agent 实现
- **LangGraph 半成品**：有已知 bug（astream 事件解析），测试 skip

### 正确做法

**LangChain（工具抽象 + LLM 调用）+ LangGraph（状态机编排）= 生产级 Agent**

LangChain 的优势在工具生态（@tool 装饰器、Tool 接口标准化），LangGraph 的优势在流程控制（条件路由、状态持久化、可观测性）。两者不是竞争关系。

---

## 2. 新架构

```
User Query
    │
    ▼
┌─────────────────────────────────────────┐
│           UnifiedAgent (LangGraph)        │
│                                           │
│  [understand] ──→ [decide] ──→ [execute] │
│       │               ▲            │      │
│       │               │            ▼      │
│       │               └──── [observe]     │
│       │                                    │
│       └──────────→ [respond] ◄───────────┘
│                                           │
│  Tools: LangChain @tool (6 tools)         │
│  LLM:   ChatOpenAI (DeepSeek/MiniMax)     │
│  State: TypedDict (messages, tool_calls)  │
└─────────────────────────────────────────┘
```

### 节点说明

| 节点 | 职责 | 实现 |
|------|------|------|
| understand | 理解意图，判断是否需要工具 | LLM 返回 JSON {action, input} |
| decide | 决定下一步路由 | 条件判断：有工具调用→execute，无→respond |
| execute | 执行工具 | LangChain ToolExecutor |
| observe | 观察结果，更新状态 | 将工具结果注入 messages |
| respond | 生成最终回答 | LLM 流式生成 |

### 优势

- **可观测**：每个节点状态可在 DAG 可视化中展示
- **可中断**：审批流程可在 execute 前挂起
- **可扩展**：新增工具只需 @tool 装饰器
- **面试价值**："我评估了三种方案后选择了 LangChain+LangGraph 组合，因为..."

---

## 3. 实施计划

### Phase 1: 创建 UnifiedAgent [P0]

文件：`app/agents/unified_agent.py`

```python
class UnifiedAgentState(TypedDict):
    messages: list
    query: str
    tool_calls: list
    iterations: int
    final_answer: str

class UnifiedAgent:
    def __init__(self):
        self.tools = [...]  # LangChain tools from TOOL_REGISTRY
        self.llm = LLMFactory.get_llm()
        self.graph = self._build_graph()
    
    async def run(self, query, tool_names=None, session_id=None) -> dict:
        # Build and execute LangGraph workflow
```

### Phase 2: 替换默认 Agent [P0]

- `config.py`：`AGENT_TYPE` 默认改为 `unified`
- `chat.py`：默认路由到 `unified_agent`
- 保留 `react` 作为 fallback 选项

### Phase 3: 更新文档 [P1]

- `interview-showcase.html`：三种 Agent → LangChain+LangGraph 组合架构
- `README.md`：更新 Agent 描述
- `DEMO_SCRIPT.md`：更新演示话术

### Phase 4: 清理 [P2]

- `langchain_agent.py`、`langgraph_agent.py` 保留但不默认使用
- `tool.py` 保留（作为简单 ReAct 参考实现）

---

## 4. 面试话术

**旧话术**（弱）：
> "我实现了三种 Agent 模式——手写 ReAct、LangChain、LangGraph，一行配置切换"

**新话术**（强）：
> "Agent 架构我评估了三种方案后选择了 LangChain+LangGraph 组合——
> LangChain 负责工具抽象和 LLM 调用，LangGraph 负责状态机编排和流程控制。
> 手写 ReAct 是学习过程，让我理解了 Thought/Action/Observation 循环的本质；
> LangGraph 的 StateGraph + conditional edges 让我实现了可观测、可中断的多步推理。"

---

## 5. 验收标准

- [ ] `unified_agent.py` 实现完整，接口与现有 `tool_agent.run()` 一致
- [ ] 6 个工具全部可用（query_inventory/query_order/create_ticket/get_datetime/get_knowledge/query_supplier）
- [ ] MAX_ITERATIONS=5 正确终止
- [ ] 审批流程在 execute 节点前正确挂起
- [ ] SSE 流式输出正常（DAG 显示 5 节点状态）
- [ ] 58/59 原有测试通过（无回归）
- [ ] `AGENT_TYPE=unified` 为默认值
