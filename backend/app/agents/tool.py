"""
SmartQA Tool Agent — LangChain + LangGraph

参考 Datawhale easy-langent 教程模式：
  - LangGraph StateGraph 做流程编排
  - LangChain ChatOpenAI + bind_tools() 做工具绑定
  - 自定义 async 工具执行节点（替代 ToolNode，直接调 tool.ainvoke()）

流程：
  START → agent(LLM决策) → 需要工具? → tools(ainvoke执行) → agent(继续)
                            → 不需要?  → END
"""
import json, logging
from typing import Optional, Annotated, Sequence
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.core.llm_router import LLMFactory
from app.core.tool_engine import get_all_tools
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


class ToolAgent:
    """LangChain + LangGraph 供应链工具 Agent"""

    def __init__(self):
        self._tools: list = []
        self._graph = None
        self._loop_call_history: list = []  # SuperPower-1: 死循环检测器，每次 run() 重置

    @property
    def name(self) -> str:
        return "ToolAgent"

    @property
    def tools(self):
        if not self._tools:
            self._tools = get_all_tools()
        return self._tools

    def _build_graph(self):
        """构建 LangGraph 状态图，使用自定义 async 工具执行节点"""
        raw_tools = self.tools
        tools_by_name = {t.name: t for t in raw_tools}

        llm = LLMFactory.get_llm(temperature=0)
        llm_with_tools = llm.bind_tools(raw_tools)

        async def agent_node(state: AgentState) -> dict:
            """LLM 决策节点（异步，不阻塞事件循环）"""
            messages = state["messages"]
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
            response = await llm_with_tools.ainvoke(messages)
            return {"messages": [response]}

        async def async_tool_node(state: AgentState) -> dict:
            """异步工具执行节点 — 直接调 tool.ainvoke()，含自愈死循环检测 (SuperPower-1)"""
            messages = state["messages"]
            last_msg = messages[-1]

            if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
                return {"messages": []}

            tool_messages = []
            for tc in last_msg.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")

                # ---- SuperPower-1: 死循环检测 ----
                args_sig = json.dumps(tool_args, sort_keys=True, ensure_ascii=True)
                current_sig = (tool_name, args_sig)
                is_loop = any(
                    prev[0] == tool_name and prev[1] == args_sig
                    for prev in self._loop_call_history
                )

                if is_loop:
                    logger.warning(
                        f"[LoopBreaker] 检测到 Agent 陷入死循环: {tool_name}({tool_args})"
                    )
                    tool_messages.append(ToolMessage(
                        content=(
                            f"⚠️ [System Alert: Loop Detected] 系统检测到你正在重复调用 {tool_name} "
                            f"并传入相同参数。这说明该数据源无法提供更多新数据。"
                            f"请立刻终止调用此工具！请结合已有信息进行合理推论，"
                            f"或调用 get_knowledge 获取背景文档，或者直接输出 Final Answer 给用户。"
                        ),
                        tool_call_id=tool_id, name=tool_name
                    ))
                    # 注入强烈的 System 提示，强制收敛
                    tool_messages.append(SystemMessage(
                        content="[System Lock] 必须在下一步输出 Final Answer，结束循环。"
                    ))
                    continue

                self._loop_call_history.append(current_sig)
                # ---- 死循环检测结束 ----

                tool = tools_by_name.get(tool_name)
                if not tool:
                    result = f"工具不存在: {tool_name}"
                else:
                    try:
                        import time as _time
                        _t0 = _time.perf_counter()
                        result_obj = await tool.ainvoke(tool_args)
                        result = result_obj if isinstance(result_obj, str) else str(result_obj)
                        _t = (_time.perf_counter() - _t0) * 1000
                        tool_metrics.record(tool_name, tool_args, result[:200], _t, True)
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                        tool_metrics.record(tool_name, tool_args, str(e)[:200], 0, False)
                        logger.error(f"[ToolAgent] {tool_name} 失败: {e}")

                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=tool_id, name=tool_name)
                )

            return {"messages": tool_messages}

        def route_after_agent(state: AgentState) -> str:
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

    # ---- Public API ----

    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict:
        """
        执行 Agent 推理。
        返回: {"answer": str, "tool_calls": list, "iterations": int}
        """
        messages = [HumanMessage(content=query)]

        # SuperPower-1: 重置死循环检测器（每次推理独立）
        self._loop_call_history = []

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
            _t_start = _time.perf_counter()

            async for event in self.graph.astream(
                {"messages": messages}, config, stream_mode="values"
            ):
                if "messages" not in event:
                    continue
                msgs = event["messages"]
                if not msgs:
                    continue
                last_msg = msgs[-1]

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

                if isinstance(last_msg, AIMessage) and last_msg.content and not last_msg.tool_calls:
                    final_answer = last_msg.content

                if iteration >= MAX_ITERATIONS:
                    final_answer = final_answer or "处理步骤过多，已终止。请简化问题后重试。"
                    break

        except Exception as e:
            logger.error(f"[ToolAgent] graph.astream 失败: {e}")
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


# Module-level singleton
tool_agent = ToolAgent()
