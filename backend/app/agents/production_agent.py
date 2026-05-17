"""生产 Agent — 负责工单创建、物料可用性检查"""
from app.agents.domain_agent import DomainAgent


class ProductionAgent(DomainAgent):
    TOOL_NAMES = ["create_ticket", "query_inventory"]


production_agent = ProductionAgent()
