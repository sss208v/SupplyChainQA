"""
SmartQA Pro - LangChain Agent 实现
============================================================
【学习要点】
1. LangChain Agent vs 手写ReAct 的对比：
   - 手写ReAct（tool.py）：完全可控，便于调试和定制，但需要自己处理解析逻辑
   - LangChain Agent：框架封装好，代码简洁，但中间过程是黑盒

2. create_react_agent 的工作原理：
   - 使用 ReAct prompt 模板引导 LLM 输出结构化的 Thought/Action/Observation
   - AgentExecutor 自动解析 LLM 输出，调用对应工具，将结果喂回 LLM
   - 直到 LLM 输出 "Final Answer" 或达到最大迭代次数

3. 本模块复用 tool_engine.py 中已有的 @tool 装饰器工具，
   无需重复定义，展示了模块化设计的好处
============================================================
"""
import logging
import asyncio
from typing import Optional
from langchain_core.tools import BaseTool
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

try:
    from langchain.agents import AgentExecutor, create_react_agent
except ImportError:
    AgentExecutor = None
    create_react_agent = None

from app.core.tool_engine import TOOL_REGISTRY, get_all_tools, get_tools_by_names
from app.core.llm_router import LLMFactory
from app.core.redis_client import chat_memory

logger = logging.getLogger(__name__)


# ---- 供应链智能助手 ReAct Prompt ----
# 与 tool.py 中的 TOOL_AGENT_PROMPT 对齐，保持一致的角色设定
LANGCHAIN_REACT_PROMPT = """你是一个供应链管理智能助手，可以使用工具来帮助用户查询库存、订单和创建工单。

你可以使用以下工具:
{tools}

请严格按照以下格式回答:

Question: 用户的问题
Thought: 你应该思考要做什么
Action: 要使用的工具名称（必须是 [{tool_names}] 中的一个）
Action Input: 工具的输入参数
Observation: 工具返回的结果
... (Thought/Action/Action Input/Observation 可以重复多次)
Thought: 我现在知道最终答案了
Final Answer: 对用户问题的最终回答

重要规则:
1. 如果问题不需要使用工具，直接给出Final Answer
2. 如果需要使用工具，严格按照Thought -> Action -> Action Input的顺序
3. 工具名称必须是上述列表中的一个
4. 仔细分析Observation的结果，决定是继续使用工具还是给出最终答案
5. 涉及库存查询、订单状态、创建工单等操作必须使用工具获取，禁止凭记忆编造
6. 请用简洁专业的语气回答，突出关键数据和异常状态

开始!

Question: {input}
{agent_scratchpad}"""


class LangChainAgent:
    """
    LangChain Agent 实现

    与 ToolAgent（手写ReAct）提供相同的接口，方便对比测试。
    内部使用 LangChain 的 create_react_agent + AgentExecutor。
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
        执行 LangChain Agent

        Args:
            query: 用户问题
            tool_names: 指定可用工具名称列表（None=全部）
            session_id: 会话ID

        Returns:
            {
                "answer": str,          # 最终回答
                "tool_calls": list,     # 工具调用记录
            }
        """
        # ---- 准备可用工具 ----
        if tool_names:
            available_tools = [
                self.tools[name] for name in tool_names if name in self.tools
            ]
        else:
            available_tools = get_all_tools()

        if not available_tools:
            return {
                "answer": "没有可用的工具，请检查工具配置。",
                "tool_calls": [],
            }

        # ---- 获取对话历史（用于日志，LangChain Agent 本身不支持 chat_history 参数） ----
        chat_history_str = ""
        if session_id and chat_memory:
            chat_history_str = await chat_memory.get_context_string(session_id)

        # ---- 构建 Agent ----
        llm = LLMFactory.get_llm(temperature=0.1, streaming=False)

        tool_names_str = ", ".join([t.name for t in available_tools])
        tools_desc = "\n".join(
            [f"- {t.name}: {t.description}" for t in available_tools]
        )

        # 注入对话历史到输入中，让 Agent 有上下文
        enhanced_query = query
        if chat_history_str:
            enhanced_query = f"对话历史：\n{chat_history_str}\n\n用户问题：{query}"

        prompt = PromptTemplate.from_template(LANGCHAIN_REACT_PROMPT)

        if create_react_agent is None or AgentExecutor is None:
            logger.warning("LangChain Agent 不可用，回退到手写 ReAct")
            from app.agents.tool import tool_agent
            return await tool_agent.run(query=query, tool_names=tool_names, session_id=session_id)

        try:
            agent = create_react_agent(
                llm=llm,
                tools=available_tools,
                prompt=prompt,
            )

            agent_executor = AgentExecutor(
                agent=agent,
                tools=available_tools,
                verbose=True,
                max_iterations=self.MAX_ITERATIONS,
                handle_parsing_errors=True,
                return_intermediate_steps=True,
            )

            # ---- 执行 Agent ----
            result = await agent_executor.ainvoke({
                "input": enhanced_query,
            })

            # ---- 解析结果 ----
            answer = result.get("output", "")
            intermediate_steps = result.get("intermediate_steps", [])

            # 将 intermediate_steps 转换为与 tool.py 一致的格式
            tool_calls = []
            for i, (agent_action, observation) in enumerate(intermediate_steps):
                tool_calls.append({
                    "iteration": i + 1,
                    "thought": agent_action.log if hasattr(agent_action, 'log') else "",
                    "tool": agent_action.tool,
                    "input": agent_action.tool_input,
                    "observation": observation,
                })

            # ---- 保存对话记忆 ----
            if session_id and chat_memory:
                await chat_memory.add_message(session_id, "user", query)
                await chat_memory.add_message(
                    session_id, "assistant", answer,
                    metadata={"tool_calls": len(tool_calls), "agent_type": "langchain"},
                )

            return {
                "answer": answer,
                "tool_calls": tool_calls,
            }

        except Exception as e:
            logger.error(f"LangChain Agent 执行失败: {e}")
            return {
                "answer": f"LangChain Agent 执行过程中出现错误: {e}",
                "tool_calls": [],
            }


# 全局单例
langchain_agent = LangChainAgent()
