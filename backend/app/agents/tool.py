"""
SmartQA Pro - 工具调用Agent
============================================================
1. ReAct = Reasoning + Acting（推理+行动）
   是当前最主流的Agent执行范式，由Google在2022年提出
   核心思想：LLM交替进行"思考"和"行动"，直到得出最终答案

2. ReAct循环：
   Thought（思考）→ Action（选择工具）→ Observation（观察结果）→ 重复或Final Answer

3. 与LangChain的create_react_agent区别：
   - LangChain的ReAct Agent是一个黑盒，你很难控制中间过程
   - 本实现是手写ReAct循环，更透明、更可控

4. 工具调用的本质：
   让LLM输出结构化的JSON（工具名+参数），
   系统解析后执行对应函数，再把结果喂回LLM
============================================================
"""
import json
import logging
from typing import Optional
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.core.llm_router import LLMFactory
from app.core.tool_engine import TOOL_REGISTRY, get_all_tools, get_tools_by_names
from app.core.redis_client import chat_memory

logger = logging.getLogger(__name__)


# ---- ReAct Prompt ----
# 1. 必须列出所有可用工具的名称和描述
# 2. 必须规定输出格式（JSON），否则LLM输出不可控
# 3. 必须给出Few-shot示例，让LLM理解格式
# 4. 必须告诉LLM"不需要工具时直接回答"
TOOL_AGENT_PROMPT = """你是一个供应链管理智能助手，可以使用工具来帮助用户查询库存、订单和创建工单。

## 可用工具：
{tools_description}

## 回答规则：
1. 如果用户的问题不需要使用工具，直接给出回答
2. 如果需要使用工具，请按以下JSON格式输出：
   {{"thought": "你的思考过程", "action": "工具名称", "action_input": "工具输入参数"}}
3. 每次只能调用一个工具
4. 观察工具返回结果后，决定是否继续调用工具或给出最终回答
5. 最终回答请用自然语言，不要包含JSON格式
6. 【重要】涉及库存查询、订单状态、创建工单等操作必须使用工具获取，禁止凭记忆编造

## 当前对话历史：
{chat_history}

## 用户问题：
{input}"""

TOOL_AGENT_STRICT_PROMPT = """你是一个供应链管理智能助手，必须使用工具来回答用户问题。

## 可用工具：
{tools_description}

## 回答规则：
1. 【强制】你必须先使用工具，再给出回答，禁止直接回答
2. 请按以下JSON格式输出：
   {{"thought": "你的思考过程", "action": "工具名称", "action_input": "工具输入参数"}}
3. 每次只能调用一个工具
4. 观察工具返回结果后，再给出最终回答
5. 最终回答请用自然语言，不要包含JSON格式
6. 请用简洁专业的语气回答，突出关键数据和异常状态

## 用户问题：
{input}"""


class ToolAgent:
    """
    工具调用Agent（手写ReAct循环）

    工作流程：
    1. 接收用户问题 + 可用工具列表
    2. LLM判断是否需要调用工具
    3. 如果需要 → 解析工具名和参数 → 执行工具 → 把结果喂回LLM
    4. 重复2-3，直到LLM给出最终回答
    5. 最多循环5次（防止死循环）
    """

    # 最大ReAct循环次数
    MAX_ITERATIONS = 5

    def __init__(self):
        self.tools = TOOL_REGISTRY

    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        user_id: str = "",
    ) -> dict:
        """
        执行工具调用

        Args:
            query: 用户问题
            tool_names: 指定可用工具名称列表（None=全部）
            session_id: 会话ID
            user_id: 用户标识（用于对话记忆隔离）

        Returns:
            {
                "answer": str,          # 最终回答
                "tool_calls": list,     # 工具调用记录
                "iterations": int,      # 循环次数
            }
        """
        # ---- 准备可用工具 ----
        if tool_names:
            available_tools = {name: self.tools[name] for name in tool_names if name in self.tools}
        else:
            available_tools = self.tools

        # 生成工具描述文本
        tools_description = self._format_tools_description(available_tools)

        # ---- 获取对话历史 ----
        chat_history_str = ""
        if session_id and chat_memory:
            chat_history_str = await chat_memory.get_context_string(session_id, user_id=user_id)

        # ---- 构建初始Prompt ----
        # 指定工具名时用严格模式，强制LLM调用工具而不是编造答案
        if tool_names:
            system_prompt = TOOL_AGENT_STRICT_PROMPT.format(
                tools_description=tools_description,
                input=query,
            )
        else:
            system_prompt = TOOL_AGENT_PROMPT.format(
                tools_description=tools_description,
                chat_history=chat_history_str or "（无历史对话）",
                input=query,
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query),
        ]

        # ---- ReAct主循环 ----
        tool_calls = []
        llm = LLMFactory.get_llm(temperature=0.1, streaming=False)

        for iteration in range(self.MAX_ITERATIONS):
            logger.info(f"ReAct循环第{iteration + 1}次")

            # 1. LLM生成回复
            response = await llm.ainvoke(messages)
            response_text = response.content.strip()

            # 2. 尝试解析为工具调用
            tool_call = self._parse_tool_call(response_text)

            if tool_call is None:
                # 没有工具调用 → 这是最终回答
                answer = self._extract_final_answer(response_text)

                # 保存对话记忆
                if session_id and chat_memory:
                    await chat_memory.add_message(session_id, "user", query, user_id=user_id)
                    await chat_memory.add_message(
                        session_id, "assistant", answer,
                        metadata={"tool_calls": len(tool_calls)},
                        user_id=user_id,
                    )

                return {
                    "answer": answer,
                    "tool_calls": tool_calls,
                    "iterations": iteration + 1,
                }

            # 3. 执行工具调用
            tool_name = tool_call["action"]
            tool_input = tool_call["action_input"]

            if tool_name not in available_tools:
                observation = f"错误：工具'{tool_name}'不存在，可用工具：{list(available_tools.keys())}"
            else:
                try:
                    tool_func = available_tools[tool_name]
                    observation = await tool_func.ainvoke(tool_input)
                    logger.info(f"工具调用成功: {tool_name}({tool_input}) = {observation}")
                except Exception as e:
                    observation = f"工具执行错误: {e}"
                    logger.error(f"工具调用失败: {tool_name}({tool_input}), 错误: {e}")

            # 记录工具调用
            tool_calls.append({
                "iteration": iteration + 1,
                "thought": tool_call.get("thought", ""),
                "tool": tool_name,
                "input": tool_input,
                "observation": observation,
            })

            # 4. 把工具结果追加到对话，继续循环
            messages.append(AIMessage(content=response_text))
            messages.append(HumanMessage(content=f"工具返回结果：{observation}\n\n请根据结果继续回答，或给出最终回答。"))

        # 超过最大循环次数，强制结束
        final_response = await llm.ainvoke(messages)
        answer = final_response.content

        return {
            "answer": answer,
            "tool_calls": tool_calls,
            "iterations": self.MAX_ITERATIONS,
        }

    @staticmethod
    def _format_tools_description(tools: dict) -> str:
        """格式化工具描述列表"""
        lines = []
        for name, tool_func in tools.items():
            description = tool_func.description if hasattr(tool_func, 'description') else "无描述"
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)

    @staticmethod
    def _parse_tool_call(text: str) -> Optional[dict]:
        """
        从LLM输出中解析工具调用

        LLM输出解析是Agent开发的核心难点之一：
        1. LLM不一定严格按JSON格式输出，可能夹杂其他文字
        2. 需要用正则提取JSON部分
        3. 解析失败时需要优雅降级（当作最终回答处理）

        生产环境的改进方案：
        - 使用OpenAI的Function Calling / Tool Calling（结构化输出）
        - 使用LangChain的PydanticOutputParser
        - 使用Structured Output（如JSON Mode）
        """
        # 用栈匹配找最外层 JSON 范围（支持嵌套）
        start = text.find('{')
        if start == -1:
            return None
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        json_str = text[start:end]

        try:
            parsed = json.loads(json_str)
            if "action" in parsed and "action_input" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

        return None

    @staticmethod
    def _extract_final_answer(text: str) -> str:
        """
        提取最终回答文本

        LLM可能在回答前加"Final Answer:"等前缀，需要清理
        """
        import re

        # 移除可能的"Final Answer:"前缀
        text = re.sub(r'^Final Answer:\s*', '', text, flags=re.IGNORECASE)

        # 如果文本中包含JSON（说明是工具调用而不是最终回答），取JSON之后的部分
        json_end = text.rfind('}')
        if json_end > 0 and json_end < len(text) - 1:
            remainder = text[json_end + 1:].strip()
            if remainder:
                return remainder

        return text


# 全局单例
tool_agent = ToolAgent()
