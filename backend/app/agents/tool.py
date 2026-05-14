"""
SmartQA Tool Agent — LangChain + LangGraph

参考 Datawhale easy-langent 教程模式：
  - LangGraph StateGraph 做流程编排
  - LangChain ChatOpenAI + bind_tools() 做工具绑定
  - ToolNode 自动处理工具执行

流程：
  START → agent(LLM决策) → 需要工具? → tools(ToolNode执行) → agent(继续)
                            → 不需要?  → END
"""
import json, logging
from typing import Optional, Annotated, Sequence
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool

from app.core.llm_router import LLMFactory
from app.core.tool_engine import get_all_tools
from app.core.redis_client import chat_memory

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

    @property
    def tools(self):
        if not self._tools:
            self._tools = get_all_tools()
        return self._tools

    def _build_graph(self):
        """构建 LangGraph 状态图"""
        llm = LLMFactory.get_llm(temperature=0)
        llm_with_tools = llm.bind_tools(self.tools)

        def agent_node(state: AgentState) -> dict:
            """LLM 决策节点"""
            messages = state["messages"]
            # 注入系统提示
            if not any(isinstance(m, SystemMessage) for m in messages):
                messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
            response = llm_with_tools.invoke(messages)
            return {"messages": [response]}

        builder = StateGraph(AgentState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode(self.tools))

        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", tools_condition)
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
    ) -> dict:
        """
        执行 Agent 推理。
        返回: {"answer": str, "tool_calls": list, "iterations": int}
        """
        messages = [HumanMessage(content=query)]

        # 加载对话历史
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
            # 用 astream 获取中间状态（同步执行，因为 langgraph 内部用 sync）
            for event in self.graph.stream(
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
                        tool_calls_record.append({
                            "tool": tc.get("name", "unknown"),
                            "input": tc.get("args", {}),
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
            logger.error(f"[ToolAgent] graph.stream 失败: {e}")
            final_answer = f"Agent 执行出错: {e}"

        if not final_answer:
            final_answer = "未能生成回答，请重试。"

        # 保存对话记忆
        if session_id and chat_memory:
            try:
                await chat_memory.add_message(session_id, "user", query)
                await chat_memory.add_message(session_id, "assistant", final_answer)
            except Exception:
                pass

        return {
            "answer": final_answer,
            "tool_calls": tool_calls_record,
            "iterations": iteration,
        }


# Module-level singleton
tool_agent = ToolAgent()
