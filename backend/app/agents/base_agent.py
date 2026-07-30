"""
SupplyChainRAG Base Agent — LangGraph ReAct Agent 基类

统一 ToolAgent 和 DomainAgent 的共享逻辑：
  - LangGraph StateGraph 构建（agent_node → async_tool_node → agent_node）
  - Loop Breaker 死循环检测（ContextVar 隔离）
  - Tool Metrics 记录
  - Chat Memory 集成
  - DEMO_MODE 降级 fallback

子类只需覆盖 TOOL_NAMES 即可获得完整的 LangGraph Agent 能力。
  - TOOL_NAMES = [] → 绑定全部工具（ToolAgent 行为）
  - TOOL_NAMES = ["query_inventory"] → 绑定子集（DomainAgent 行为）
"""
import json
import logging
import contextvars
from typing import Optional, Annotated, Sequence
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.core.llm_router import LLMFactory
from app.core.tool_engine import get_all_tools, TOOL_REGISTRY
from app.core.redis_client import chat_memory
from app.core.tool_metrics import tool_metrics
from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

# 每请求独立的死循环检测历史（ContextVar 保证并发隔离）
_loop_call_history_var: contextvars.ContextVar[list] = contextvars.ContextVar(
    'loop_call_history', default=[]
)

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


def _build_demo_fallback(agent_name: str, query: str, error: str) -> str:
    """构建 DEMO_MODE 下的结构化降级响应"""
    return (
        f"[演示模式] {agent_name} 收到问题：「{query}」\n\n"
        f"当前状态：LLM 未连接（{error[:80]}）\n"
        f"可演示能力：\n"
        f"  • 工具调用链路（query_inventory / query_order / create_ticket 等 6 个工具）\n"
        f"  • RAG 混合检索（Milvus 向量 + BM25 关键词 → RRF 融合 → Reranker 精排）\n"
        f"  • 行级权限过滤（Milvus ARRAY 字段 + array_contains 实时过滤）\n"
        f"  • SSE 流式输出（tool_status → text → done 三事件流）\n\n"
        f"接入 DeepSeek API 后即可获得真实 Agent 推理结果。"
    )


class BaseReActAgent:
    """LangGraph ReAct Agent 基类

    子类覆盖 TOOL_NAMES 即可：
      - 空列表 [] → 绑定全部工具
      - ["query_inventory"] → 绑定子集
    """

    TOOL_NAMES: list[str] = []

    def __init__(self):
        self._tools: list = []
        self._graph = None

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def tools(self):
        if not self._tools:
            if self.TOOL_NAMES:
                self._tools = [TOOL_REGISTRY[n] for n in self.TOOL_NAMES if n in TOOL_REGISTRY]
            else:
                self._tools = get_all_tools()
        return self._tools

    def _build_graph(self):
        """构建 LangGraph 状态图，使用自定义 async 工具执行节点"""
        raw_tools = self.tools
        tools_by_name = {t.name: t for t in raw_tools}
        agent_name = self.__class__.__name__  # 闭包引用

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
            """异步工具执行节点 — 直接调 tool.ainvoke()，含死循环检测"""
            messages = state["messages"]
            last_msg = messages[-1]

            if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
                return {"messages": []}

            tool_messages = []
            for tc in last_msg.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")

                # ---- 死循环检测 ----
                args_sig = json.dumps(tool_args, sort_keys=True, ensure_ascii=True)
                current_sig = (tool_name, args_sig)
                is_loop = any(
                    prev[0] == tool_name and prev[1] == args_sig
                    for prev in _loop_call_history_var.get([])
                )

                if is_loop:
                    logger.warning(
                        f"[{agent_name}][LoopBreaker] 检测到死循环: {tool_name}({tool_args})"
                    )
                    tool_messages.append(ToolMessage(
                        content=(
                            f"[警告] [System Alert: Loop Detected] 系统检测到你正在重复调用 {tool_name} "
                            f"并传入相同参数。请结合已有信息进行推论或直接输出 Final Answer。"
                        ),
                        tool_call_id=tool_id, name=tool_name
                    ))
                    tool_messages.append(SystemMessage(
                        content="[System Lock] 必须在下一步输出 Final Answer，结束循环。"
                    ))
                    continue

                history = list(_loop_call_history_var.get([]))
                history.append(current_sig)
                _loop_call_history_var.set(history)
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

    def _handle_error(self, query: str, error: Exception) -> tuple[str, Optional[dict]]:
        """错误处理 hook — 子类可覆盖（如 DEMO_MODE 降级）

        Returns:
            (answer, demo_info) — demo_info 为 None 表示非演示模式
        """
        settings = get_settings()
        if settings.DEMO_MODE:
            answer = _build_demo_fallback(self.name, query, str(error))
            demo_info = {
                "mode": "demo",
                "reason": f"LLM 不可用: {str(error)[:120]}",
                "suggested_next_step": "接入 DeepSeek API 后即可获得真实推理结果。当前可演示：工具调用链路、RAG检索、权限过滤。",
                "summary": f"{self.name} 已接收问题，当前为离线演示模式"
            }
            return answer, demo_info
        return f"Agent 执行出错: {error}", None

    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict:
        """执行 Agent 推理，返回 {answer, tool_calls, iterations}"""
        messages = [HumanMessage(content=query)]

        # 重置死循环检测器（每次请求独立）
        _loop_call_history_var.set([])

        if session_id and chat_memory:
            try:
                history = await chat_memory.get_context_string(session_id, user_id=user_id or "")
                if history:
                    messages = [HumanMessage(content=f"对话历史：\n{history}\n\n当前问题：{query}")]
            except Exception as e:
                logger.debug(f"[Agent] 对话记忆加载失败: {e}")

        config = {"configurable": {"thread_id": session_id or "default"}}

        tool_calls_record = []
        final_answer = ""
        iteration = 0
        demo_info = None

        try:
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
            final_answer, demo_info = self._handle_error(query, e)

        if not final_answer:
            final_answer = "未能生成回答，请重试。"

        # Build return dict
        result = {
            "answer": final_answer,
            "tool_calls": tool_calls_record,
            "iterations": iteration,
        }
        if demo_info:
            result["demo_info"] = demo_info

        if session_id and chat_memory:
            try:
                await chat_memory.add_message(session_id, "user", query, user_id=user_id)
                await chat_memory.add_message(session_id, "assistant", final_answer, user_id=user_id)
            except Exception as e:
                logger.debug(f"[Agent] 对话记忆保存失败: {e}")

        return result
