"""库存 Agent — 负责物料库存查询、在途库存追踪"""
from app.agents.domain_agent import DomainAgent


class InventoryAgent(DomainAgent):
    TOOL_NAMES = ["query_inventory"]


inventory_agent = InventoryAgent()
