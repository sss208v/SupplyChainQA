"""
Agent 路由器 — 按工具名分发到对应专域 Agent

工具 → Agent 映射表（每个工具有一个主 Agent）：
  query_inventory → InventoryAgent
  query_order     → PurchaseAgent
  query_supplier  → PurchaseAgent
  get_knowledge   → QualityAgent
  create_ticket   → ProductionAgent
  get_datetime    → 回退到通用 ToolAgent
"""
import logging
from typing import Optional

from app.agents.tool import tool_agent  # 通用 Agent（兜底）
from app.agents.purchase_agent import purchase_agent
from app.agents.inventory_agent import inventory_agent
from app.agents.quality_agent import quality_agent
from app.agents.production_agent import production_agent

logger = logging.getLogger(__name__)

# 工具名 → 专域 Agent 映射
TOOL_AGENT_MAP = {
    "query_inventory": inventory_agent,
    "query_order":     purchase_agent,
    "query_supplier":  purchase_agent,
    "get_knowledge":   quality_agent,
    "create_ticket":   production_agent,
    # get_datetime 回退到通用 tool_agent
}

# 所有已注册的专域 Agent（用于 Orchestrator 遍历）
ALL_DOMAIN_AGENTS = [
    purchase_agent,
    inventory_agent,
    quality_agent,
    production_agent,
]


def get_agent_for_tool(tool_name: Optional[str] = None):
    """根据工具名获取对应的专域 Agent，无匹配时返回通用 ToolAgent"""
    if tool_name and tool_name in TOOL_AGENT_MAP:
        agent = TOOL_AGENT_MAP[tool_name]
        logger.debug(f"[AgentRouter] 路由到 {agent.name}: tool={tool_name}")
        return agent
    logger.debug(f"[AgentRouter] 回退到通用 ToolAgent: tool={tool_name}")
    return tool_agent


def get_agent_by_name(name: str):
    """按 Agent 类名获取实例"""
    name_lower = name.lower()
    for agent in ALL_DOMAIN_AGENTS:
        if agent.name.lower() == name_lower:
            return agent
    if name_lower in ("toolagent", "tool_agent"):
        return tool_agent
    return None
