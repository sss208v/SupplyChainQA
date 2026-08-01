"""
SupplyChainRAG Tool Agent — 通用 Agent（绑定全部工具）

基于 BaseReActAgent 基类，TOOL_NAMES=[] 表示绑定全部工具。
本文件含向后兼容 re-export，真实实现（修改请落在这些文件）：
- BaseReActAgent / _loop_call_history_var / MAX_ITERATIONS -> app/agents/base_agent.py
- get_all_tools -> app/core/tool_engine.py
- LLMFactory   -> app/core/llm_router.py
"""
from app.agents.base_agent import BaseReActAgent, _loop_call_history_var, MAX_ITERATIONS
from app.core.tool_engine import get_all_tools  # re-export for backward compat (test mock paths)
from app.core.llm_router import LLMFactory  # re-export for backward compat (test mock paths)

# 向后兼容：测试文件从 tool.py 导入这些名称
__all__ = ["ToolAgent", "tool_agent", "_loop_call_history_var", "MAX_ITERATIONS",
           "get_all_tools", "LLMFactory"]


class ToolAgent(BaseReActAgent):
    """通用工具 Agent，绑定 TOOL_REGISTRY 中的全部工具。"""
    TOOL_NAMES = []  # 空 = 全部


# Module-level singleton
tool_agent = ToolAgent()
