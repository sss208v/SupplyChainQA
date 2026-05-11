"""
SmartQA Pro - LangGraph Agent 实现
============================================================
用 LangGraph StateGraph 实现等价于 tool.py 的 ReAct 循环。

StateGraph 架构：
  [start] → router_node → tool_node → observer_node → decide_node
                                                  ↑              │
                                                  └─ continue ──┘
                                                               ↓
                                                          [finish] → [end]

- router_node : 调用 LLM 判断是否需要工具，返回 action 或 finish
- tool_node   : 执行工具，复用 TOOL_REGISTRY
- observer_node: 从工具结果中提取文本作为 observation
- decide_node : 判断是否继续（MAX_ITERATIONS=5）
============================================================
"""
import json
import logging
from typing import Optional, TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, END
from langgraph.types import Command
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.core.llm_router import LLMFactory
from app.core.tool_engine import TOOL_REGISTRY, get_all_tools, get_tools_by_names
from app.core.redis_client import chat_memory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], None]
    tool_calls: list[dict]
    iterations: int
    last_tool_result: Optional[str]
    final_answer: Optional[str]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_ITERATIONS = 5

SYSTEM_PROMPT = """你是一个供应链管理智能助手，可以使用工具来帮助用户查询库存、订单和创建工单。

## 回答规则
1. 如果用户的问题不需要使用工具，直接给出回答
2. 如果需要使用工具，请按以下JSON格式输出：
   {{"action": "工具名称", "action_input": "工具输入参数"}}
3. 每次只能调用一个工具
4. 观察工具返回结果后，决定是否继续调用工具或给出最终回答
5. 最终回答请用自然语言，不要包含JSON格式
6. 【重要】涉及库存查询、订单状态、创建工单等操作必须使用工具获取，禁止凭记忆编造

## 可用工具
{tools_description}
"""


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def _format_tools_description(available_tools: dict[str, BaseTool]) -> str:
    """生成工具描述文本，供 LLM 理解工具接口。"""
    lines = []
    for name, tool in available_tools.items():
        desc = getattr(tool, "description", "") or ""
        params = ""
        if hasattr(tool, "get_input_schema") and callable(tool.get_input_schema):
            try:
                schema = tool.get_input_schema()
                if hasattr(schema, "model_json_schema"):
                    params = str(schema.model_json_schema())
                else:
                    params = str(schema)
            except Exception:
                pass
        lines.append(f"- {name}: {desc}  参数: {params}")
    return "\n".join(lines)


def _build_tools_description(available_tools: dict[str, BaseTool]) -> str:
    """生成 LangChain ToolNode 所需的工具列表描述。"""
    return "\n".join(
        f"- {name}: {getattr(tool, 'description', '') or ''}"
        for name, tool in available_tools.items()
    )


async def _call_llm(state: AgentState, available_tools: dict[str, BaseTool]) -> dict:
    """调用 LLM，根据消息历史判断下一步 action 或直接回答。"""
    from app.config import get_settings
    settings = get_settings()

    tools_desc = _format_tools_description(available_tools)
    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(tools_description=tools_desc))

    messages = [system_msg] + list(state["messages"])
    llm = LLMFactory.get_llm(provider=settings.LLM_PROVIDER, temperature=0.7, streaming=False)
    response = await llm.ainvoke(messages)

    raw = response.content.strip()

    # 尝试解析 LLM 返回的 JSON
    try:
        parsed = json.loads(raw)
        action = parsed.get("action", "").strip()
        action_input = parsed.get("action_input", "")

        if action and action in available_tools:
            return {
                "action": action,
                "action_input": action_input,
            }
    except (json.JSONDecodeError, AttributeError):
        pass

    # 非 JSON 格式或不需要工具 → 当作最终回答
    return {
        "action": "__FINISH__",
        "action_input": raw,
    }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(available_tools: dict[str, BaseTool]):
    """
    构建 LangGraph StateGraph。
    返回 (graph, router_node, tool_node, observer_node, decide_node)
    """
    from langgraph.graph import StateGraph

    # ---- Node 1: router_node ----
    async def router_node(state: AgentState) -> dict:
        try:
            result = await _call_llm(state, available_tools)
        except Exception as e:
            result = {"action": "__FINISH__", "action_input": f"LLM调用失败: {e}"}

        action = result.get("action", "")
        action_input = result.get("action_input", "")

        if action == "__FINISH__":
            # Direct answer: capture it here so the final event carries it
            return {"router_output": result, "final_answer": str(action_input)}

        return {"router_output": result}

    # ---- Node 2: tool_node ----
    async def tool_node(state: AgentState) -> dict:
        router_out = state.get("router_output", {})
        action = router_out.get("action", "")
        action_input = router_out.get("action_input", {})

        # 单字符串参数兼容
        if isinstance(action_input, str) and available_tools:
            tool_name = action if action else list(available_tools.keys())[0]
            if tool_name not in available_tools:
                tool_name = list(available_tools.keys())[0]
            tool = available_tools[tool_name]
            try:
                result = await tool.ainvoke(action_input)
            except Exception:
                result = f"工具调用失败: {action_input}"
            return {"last_tool_result": str(result)}

        if action and action in available_tools:
            tool = available_tools[action]
            try:
                result = await tool.ainvoke(action_input)
            except Exception as e:
                result = f"工具调用失败: {e}"
        else:
            result = f"未知工具: {action}"

        return {"last_tool_result": str(result)}

    # ---- Node 3: observer_node ----
    async def observer_node(state: AgentState) -> dict:
        tool_result = state.get("last_tool_result", "")
        new_msg = AIMessage(content=f"[Observation] {tool_result}")
        return {
            "messages": [new_msg],
            "tool_calls": state["tool_calls"] + [
                {
                    "tool": state.get("router_output", {}).get("action", ""),
                    "input": state.get("router_output", {}).get("action_input", ""),
                    "output": tool_result,
                }
            ],
            # iterations = number of tool calls so far (1-based)
            "iterations": len(state["tool_calls"]) + 1,
        }

    # ---- Node 4: decide_node ----
    def decide_node(state: AgentState) -> Command:
        router_out = state.get("router_output", {})
        action = router_out.get("action", "") if router_out else ""

        if action == "__FINISH__":
            return Command(goto=END, update={"final_answer": router_out.get("action_input", "") if router_out else ""})

        iterations = state.get("iterations", 0)
        if iterations >= MAX_ITERATIONS:
            return Command(goto=END, update={"final_answer": "已达到最大迭代次数。"})

    # ---- Build graph ----
    workflow = StateGraph(AgentState)

    workflow.add_node("router", router_node)
    workflow.add_node("tool", tool_node)
    workflow.add_node("observer", observer_node)
    workflow.add_node("decide", decide_node)

    # Start → router
    workflow.set_entry_point("router")

    # router → tool (needs tool) or END (direct answer)
    def route_from_router(state: AgentState) -> str:
        router_out = state.get("router_output", {})
        action = router_out.get("action", "") if router_out else ""
        if action == "__FINISH__":
            return END
        return "tool"

    workflow.add_conditional_edges(
        "router",
        route_from_router,
        {"tool": "tool", END: END},
    )

    # tool → observer → decide
    workflow.add_edge("tool", "observer")
    workflow.add_edge("observer", "decide")

    return workflow.compile()


# ---------------------------------------------------------------------------
# LangGraphAgent
# ---------------------------------------------------------------------------

class LangGraphAgent:
    """
    工具调用 Agent（LangGraph StateGraph 实现）

    工作流程：
    1. 接收用户问题 + 可用工具列表
    2. router_node 调用 LLM 判断是否需要工具
    3. 需要 → tool_node 执行工具 → observer_node 提取结果
    4. decide_node 判断是否继续（最多 MAX_ITERATIONS 次）
    5. 循环直到 LLM 输出最终回答或达到迭代上限
    """

    MAX_ITERATIONS = 5

    def __init__(self):
        self.tools = TOOL_REGISTRY

    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """
        执行 LangGraph Agent

        Args:
            query: 用户问题
            tool_names: 指定可用工具名称列表（None=全部）
            session_id: 会话ID

        Returns:
            {
                "answer": str,          # 最终回答
                "tool_calls": list,     # 工具调用记录
                "iterations": int,     # 循环次数
            }
        """
        if tool_names:
            available_tools = {name: self.tools[name] for name in tool_names if name in self.tools}
        else:
            available_tools = self.tools

        chat_history_str = ""
        if session_id and chat_memory:
            try:
                history = chat_memory.get_history(session_id)
                if history:
                    chat_history_str = "\n".join(
                        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                        for m in history[-10:]
                    )
            except Exception:
                pass

        # Build graph per call so that available_tools is dynamic
        graph = build_graph(available_tools)

        initial_state: AgentState = {
            "messages": [HumanMessage(content=f"## 对话历史\n{chat_history_str}\n\n## 用户问题\n{query}")],
            "tool_calls": [],
            "iterations": 0,
            "last_tool_result": None,
            "should_continue": True,
            "router_output": None,
        }

        config = {"recursion_limit": MAX_ITERATIONS * 3 + 10}

        final_answer = ""
        tool_calls_record = []
        iterations = 0

        async for event in graph.astream(initial_state, config=config):
            # 每个 step 会产生一个 node 的输出
            for node_name, node_output in event.items():
                if node_output and isinstance(node_output, dict):
                    # 从 router 节点提取 final_answer（当 __FINISH__ 时）
                    if node_output.get("final_answer"):
                        final_answer = node_output["final_answer"]
                    # 从 decide 节点提取 final_answer
                    if node_name == "decide" and node_output.get("final_answer"):
                        final_answer = node_output["final_answer"]
                    # 从 observer 节点记录工具调用
                    if node_name == "observer":
                        tool_calls_record = node_output.get("tool_calls", tool_calls_record)
                if node_name == "decide":
                    iterations += 1

        # fallback: 从最后一条消息内容提取
        if not final_answer and initial_state["messages"]:
            last_msg = initial_state["messages"][-1]
            if hasattr(last_msg, "content") and last_msg.content:
                final_answer = last_msg.content

        return {
            "answer": final_answer,
            "tool_calls": tool_calls_record,
            "iterations": iterations,
        }

# ---- 模块级单例 ----
langgraph_agent = LangGraphAgent()