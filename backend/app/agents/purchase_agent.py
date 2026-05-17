"""采购 Agent — 负责采购订单查询、供应商信息查询"""
from app.agents.domain_agent import DomainAgent


class PurchaseAgent(DomainAgent):
    TOOL_NAMES = ["query_order", "query_supplier"]


purchase_agent = PurchaseAgent()
