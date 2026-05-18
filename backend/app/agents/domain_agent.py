"""
SmartQA Domain Agents — 供应链专域 Agent 基类

每个专域 Agent 绑定特定的工具子集，共享同一套 LangGraph StateGraph 框架。
修复：使用自定义 async 工具执行节点替代 ToolNode，直接调用 tool.ainvoke()。
"""
import asyncio
import json
import logging
from typing import Optional, Annotated, Sequence
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.core.llm_router import LLMFactory
from app.core.tool_engine import TOOL_REGISTRY
from app.core.redis_client import chat_memory
from app.core.tool_metrics import tool_metrics

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

SYSTEM_PROMPT = """你是一个供应链智能助手，可以查询实时库存、采购订单、供应商信息和知识库。

规则：
1. 用户问实时数据（库存/订单/供应商/时间），调用对应工具
2. 用户问知识类问题，调用 get_knowledge 检索知识库
3. 用户要创建工单，调用 create_ticket（会触发审批）
4. 一个工具不够时，可以多次调用
5. 获取到足够信息后，用中文给出完整、准确的回答
6. 如果信息不足，诚实告知用户需要补充什么"""


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


class DomainAgent:
    """供应链专域 Agent 基类

    每个子类只需定义 TOOL_NAMES 即可获得完整的 LangGraph Agent 能力。
    """

    TOOL_NAMES: list[str] = []  # 子类覆盖

    def __init__(self):
        self._tools: list = []
        self._graph = None

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def tools(self):
        if not self._tools:
            self._tools = [TOOL_REGISTRY[n] for n in self.TOOL_NAMES if n in TOOL_REGISTRY]
        return self._tools

    def _build_graph(self):
        """构建 LangGraph 状态图，使用自定义 async 工具执行节点"""
        raw_tools = self.tools
        tools_by_name = {t.name: t for t in raw_tools}
        agent_name = self.__class__.__name__  # 闭包引用，避免 self 作用域问题

        llm = LLMFactory.get_llm(temperature=0)
        llm_with_tools = llm.bind_tools(raw_tools)

        def agent_node(state: AgentState) -> dict:
            """LLM 决策节点"""
            messages = state["messages"]
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
            response = llm_with_tools.invoke(messages)
            return {"messages": [response]}

        async def async_tool_node(state: AgentState) -> dict:
            """异步工具执行节点 — 直接调 tool.ainvoke()"""
            messages = state["messages"]
            last_msg = messages[-1]

            if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
                return {"messages": []}

            tool_messages = []
            for tc in last_msg.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")

                tool = tools_by_name.get(tool_name)
                if not tool:
                    result = f"工具不存在: {tool_name}"
                else:
                    try:
                        import time as _time
                        _t0 = _time.perf_counter()
                        # 关键修复：直接调 ainvoke，不走 sync invoke
                        result_obj = await tool.ainvoke(tool_args)
                        result = result_obj if isinstance(result_obj, str) else str(result_obj)
                        _t = (_time.perf_counter() - _t0) * 1000
                        tool_metrics.record(tool_name, tool_args, result[:200], _t, True)
                        logger.info(f"[{agent_name}] {tool_name} 完成: {_t:.0f}ms")
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                        tool_metrics.record(tool_name, tool_args, str(e)[:200], 0, False)
                        logger.error(f"[{agent_name}] {tool_name} 失败: {e}")

                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=tool_id, name=tool_name)
                )

            return {"messages": tool_messages}

        def route_after_agent(state: AgentState) -> str:
            """路由：需要工具 → tools_node，否则 → END"""
            messages = state["messages"]
            last_msg = messages[-1] if messages else None
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                return "tools"
            return END

        builder = StateGraph(AgentState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", async_tool_node)

        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")

        return builder.compile()

    @property
    def graph(self):
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph

    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict:
        """执行 Agent 推理，返回 {answer, tool_calls, iterations}"""
        messages = [HumanMessage(content=query)]

        if session_id and chat_memory:
            try:
                history = await chat_memory.get_context_string(session_id)
                if history:
                    messages = [HumanMessage(content=f"对话历史：\n{history}\n\n当前问题：{query}")]
            except Exception:
                pass

        config = {"configurable": {"thread_id": session_id or "default"}}

        tool_calls_record = []
        final_answer = ""
        iteration = 0

        try:
            import time as _time

            # 使用 astream 获取中间状态
            async for event in self.graph.astream(
                {"messages": messages}, config, stream_mode="values"
            ):
                if "messages" not in event:
                    continue
                msgs = event["messages"]
                if not msgs:
                    continue
                last_msg = msgs[-1]

                # 记录工具调用
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    for tc in last_msg.tool_calls:
                        tool_name = tc.get("name", "unknown")
                        tool_input = tc.get("args", {})
                        tool_calls_record.append({
                            "tool": tool_name,
                            "input": tool_input,
                        })
                        iteration += 1
                        if iteration >= MAX_ITERATIONS:
                            break

                # 记录最终回答
                if isinstance(last_msg, AIMessage) and last_msg.content and not last_msg.tool_calls:
                    final_answer = last_msg.content

                if iteration >= MAX_ITERATIONS:
                    final_answer = final_answer or "处理步骤过多，已终止。请简化问题后重试。"
                    break

        except Exception as e:
            logger.error(f"[{self.name}] graph.astream 失败: {e}")
            final_answer = f"Agent 执行出错: {e}"

        if not final_answer:
            final_answer = "未能生成回答，请重试。"

        if session_id and chat_memory:
            try:
                await chat_memory.add_message(session_id, "user", query, user_id=user_id)
                await chat_memory.add_message(session_id, "assistant", final_answer, user_id=user_id)
            except Exception:
                pass

        return {
            "answer": final_answer,
            "tool_calls": tool_calls_record,
            "iterations": iteration,
        }
