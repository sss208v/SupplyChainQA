"""
SupplyChainRAG Domain Agents — 供应链专域 Agent 基类

每个专域 Agent 绑定特定的工具子集，共享同一套 LangGraph StateGraph 框架。
子类只需定义 TOOL_NAMES 即可获得完整的 LangGraph Agent 能力。
"""
from app.agents.base_agent import BaseReActAgent, _build_demo_fallback


class DomainAgent(BaseReActAgent):
    """供应链专域 Agent 基类

    每个子类只需定义 TOOL_NAMES 即可获得完整的 LangGraph Agent 能力。
    TOOL_NAMES 为空时绑定全部工具（继承自 BaseReActAgent）。
    """
    TOOL_NAMES: list[str] = []  # 子类覆盖


# 为了向后兼容，暴露 _build_demo_fallback
__all__ = ["DomainAgent", "_build_demo_fallback"]
