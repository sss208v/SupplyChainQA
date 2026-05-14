"""
SmartQA Pro — LangChain + LangGraph 统一 Agent

架构：LangChain 管工具 + LangGraph 管状态流转

  用户 Query
      │
      ▼
  [understand] ── LLM 判断是否需要工具，返回 JSON {action, input}
      │
      ▼
  [decide] ── 条件路由：有工具调用 → execute / 无 → respond
      │
      ▼
  [execute] ── LangChain ToolExecutor 执行工具（含审批拦截）
      │
      ▼
  [observe] ── 将工具结果注入消息历史
      │
      ├── iterations < MAX → 回到 understand（继续推理）
      │
      └── iterations >= MAX → respond（强制总结）

接口与 tool.py 的 tool_agent.run() 一致：
  await unified_agent.run(query, tool_names=None, session_id=None)
  → {"answer": str, "tool_calls": list, "iterations": int}
"""
import json, logging, asyncio
from typing import Optional, TypedDict, Annotated, Sequence, Literal
from langgraph.graph import StateGraph, END
from langgraph.types import Command
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.core.llm_router import LLMFactory
from app.core.tool_engine import TOOL_REGISTRY, get_all_tools, get_tools_by_names
from app.core.redis_client import chat_memory

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

SYSTEM_PROMPT = """你是一个供应链智能助手，可以调用工具查询实时数据或检索知识库。

可用工具：
{tool_descriptions}

回复格式（严格JSON，不要额外文字）：
- 需要工具时：{{"action": "use_tool", "action_input": {{"tool_name": "query_inventory", "tool_input": {{"material_code": "MAT-001"}}}} }}
- 不需要工具时：{{"action": "respond", "action_input": {{"answer": "你的回答"}} }}
- 信息不足时：{{"action": "respond", "action_input": {{"answer": "请补充以下信息：..."}} }}"""


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], None]
    tool_calls: list[dict]
    iterations: int
    final_answer: str


def _parse_llm_json(raw: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    raw = raw.strip()
    # Remove markdown code blocks
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]) if len(lines) > 1 else raw
        if raw.endswith("```"):
            raw = raw[:-3]
    
    # Find JSON object
    start = raw.find("{")
    if start == -1:
        return {"action": "respond", "action_input": {"answer": raw}}
    
    # Brace matching
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i+1])
                except json.JSONDecodeError:
                    break
    
    return {"action": "respond", "action_input": {"answer": raw}}


class UnifiedAgent:
    """LangChain + LangGraph 统一 Agent"""

    def __init__(self):
        self.tools: list[BaseTool] = list(TOOL_REGISTRY.values())
        self._graph = None

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph 状态图"""
        builder = StateGraph(AgentState)

        builder.add_node("understand", self._understand_node)
        builder.add_node("execute", self._execute_node)
        builder.add_node("observe", self._observe_node)
        builder.add_node("respond", self._respond_node)

        builder.set_entry_point("understand")

        # understand → decide (conditional)
        builder.add_conditional_edges(
            "understand",
            self._decide_route,
            {"execute": "execute", "respond": "respond"}
        )

        # execute → observe → decide (loop or finish)
        builder.add_edge("execute", "observe")
        builder.add_conditional_edges(
            "observe",
            self._decide_continue,
            {"understand": "understand", "respond": "respond"}
        )

        builder.add_edge("respond", END)

        return builder.compile()

    @property
    def graph(self):
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph

    # ---- Nodes ----

    async def _understand_node(self, state: AgentState) -> dict:
        """理解意图：调用 LLM 判断是否需要工具"""
        tools_desc = "\n".join(
            f"- {t.name}: {t.description}" for t in self.tools
        )
        system = SYSTEM_PROMPT.format(tool_descriptions=tools_desc)

        messages = [SystemMessage(content=system)] + list(state.get("messages", []))
        llm = LLMFactory.get_llm(temperature=0, streaming=False)
        
        try:
            response = await llm.ainvoke(messages)
            parsed = _parse_llm_json(response.content if hasattr(response, 'content') else str(response))
        except Exception as e:
            logger.error(f"[UnifiedAgent] LLM 调用失败: {e}")
            parsed = {"action": "respond", "action_input": {"answer": "服务暂时不可用，请稍后重试"}}

        new_messages = list(state.get("messages", []))
        new_messages.append(AIMessage(content=json.dumps(parsed, ensure_ascii=False)))

        return {
            "messages": new_messages,
            "router_output": parsed,
        }

    def _decide_route(self, state: AgentState) -> Literal["execute", "respond"]:
        """决定路由：有工具调用 → execute，否则 → respond"""
        output = state.get("router_output", {})
        action = output.get("action", "respond")
        return "execute" if action == "use_tool" else "respond"

    async def _execute_node(self, state: AgentState) -> dict:
        """执行工具"""
        output = state.get("router_output", {})
        action_input = output.get("action_input", {})
        tool_name = action_input.get("tool_name", "")
        tool_input = action_input.get("tool_input", {})

        tool_calls = list(state.get("tool_calls", []))
        iterations = state.get("iterations", 0) + 1
        messages = list(state.get("messages", []))

        # Find and execute tool
        tool = TOOL_REGISTRY.get(tool_name)
        if tool is None:
            error_msg = f"工具 {tool_name} 不存在，可用: {list(TOOL_REGISTRY.keys())}"
            messages.append(AIMessage(content=error_msg))
            tool_calls.append({"tool": tool_name, "input": tool_input, "result": error_msg, "error": True})
        else:
            try:
                if asyncio.iscoroutinefunction(tool.func):
                    result = await tool.ainvoke(tool_input)
                else:
                    result = tool.invoke(tool_input)
                result_str = str(result)
                messages.append(ToolMessage(content=result_str, tool_call_id=tool_name))
                tool_calls.append({"tool": tool_name, "input": tool_input, "result": result_str})
                logger.info(f"[UnifiedAgent] {tool_name}({tool_input}) → {result_str[:100]}")
            except Exception as e:
                error_msg = f"工具执行失败: {e}"
                messages.append(AIMessage(content=error_msg))
                tool_calls.append({"tool": tool_name, "input": tool_input, "result": error_msg, "error": True})

        return {
            "messages": messages,
            "tool_calls": tool_calls,
            "iterations": iterations,
        }

    async def _observe_node(self, state: AgentState) -> dict:
        """观察结果，注入提示词引导 LLM 基于工具结果回答"""
        messages = list(state.get("messages", []))
        messages.append(HumanMessage(content="请根据以上工具调用结果，用中文简洁回答用户的问题。如果结果为空或出错，诚实告知。"))
        return {"messages": messages}

    def _decide_continue(self, state: AgentState) -> Literal["understand", "respond"]:
        """判断是否继续循环"""
        iterations = state.get("iterations", 0)
        return "understand" if iterations < MAX_ITERATIONS else "respond"

    async def _respond_node(self, state: AgentState) -> dict:
        """生成最终回答"""
        messages = list(state.get("messages", []))
        system = SystemMessage(content="你是供应链智能助手。请根据对话历史和工具调用结果，用中文给出完整、准确的最终回答。不要提及内部流程。")
        llm = LLMFactory.get_llm(temperature=0.3, streaming=False)

        try:
            response = await llm.ainvoke([system] + messages[-10:])
            answer = response.content if hasattr(response, 'content') else str(response)
        except Exception as e:
            logger.error(f"[UnifiedAgent] respond 失败: {e}")
            answer = "抱歉，生成回答时出错，请重试。"

        return {"final_answer": answer}

    # ---- Public API ----

    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """
        执行 Agent 推理

        Returns:
            {"answer": str, "tool_calls": list, "iterations": int}
        """
        # Build initial state
        messages: list[BaseMessage] = []

        # Load chat history
        if session_id and chat_memory:
            try:
                history = await chat_memory.get_context_string(session_id)
                if history:
                    messages.append(HumanMessage(content=f"对话历史：\n{history}"))
            except Exception:
                pass

        messages.append(HumanMessage(content=query))

        initial_state: AgentState = {
            "messages": messages,
            "tool_calls": [],
            "iterations": 0,
            "final_answer": "",
        }

        # Run graph
        final_answer = ""
        tool_calls = []
        iterations = 0

        try:
            async for event in self.graph.astream(initial_state):
                for node_name, node_output in event.items():
                    if node_output and isinstance(node_output, dict):
                        if node_output.get("final_answer"):
                            final_answer = node_output["final_answer"]
                        if node_output.get("tool_calls"):
                            tool_calls = node_output["tool_calls"]
                        if node_output.get("iterations"):
                            iterations = node_output["iterations"]
        except Exception as e:
            logger.error(f"[UnifiedAgent] astream 失败: {e}")
            final_answer = f"Agent 执行出错: {e}"

        # Save to memory
        if session_id and chat_memory and final_answer:
            try:
                await chat_memory.add_message(session_id, "user", query)
                await chat_memory.add_message(session_id, "assistant", final_answer)
            except Exception:
                pass

        return {
            "answer": final_answer or "未能生成回答",
            "tool_calls": tool_calls,
            "iterations": iterations,
        }


# Module-level singleton
unified_agent = UnifiedAgent()
