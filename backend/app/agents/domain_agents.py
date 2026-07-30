"""
SupplyChainRAG - 领域 Agent 统一注册
============================================================
四个领域 Agent 合并管理，每个只覆盖 DomainAgent + TOOL_NAMES 配置。

- InventoryAgent: 物料库存查询
- PurchaseAgent: 采购订单 + 供应商查询
- QualityAgent: 知识库检索 + 质量工单
- ProductionAgent: 工单创建 + 物料检查
============================================================
"""
from app.agents.domain_agent import DomainAgent


class InventoryAgent(DomainAgent):
    """库存 Agent — 负责物料库存查询、在途库存追踪"""
    TOOL_NAMES = ["query_inventory"]


class PurchaseAgent(DomainAgent):
    """采购 Agent — 负责采购订单查询、供应商信息查询"""
    TOOL_NAMES = ["query_order", "query_supplier"]


class QualityAgent(DomainAgent):
    """质量 Agent — 负责知识库检索（质量标准/规范）、质量异常工单"""
    TOOL_NAMES = ["get_knowledge", "create_ticket", "track_logistics"]


class ProductionAgent(DomainAgent):
    """生产 Agent — 负责工单创建、物料可用性检查"""
    TOOL_NAMES = [
        "create_ticket", "query_inventory",
        "track_logistics", "calculate_reorder_point",
    ]


# 全局单例
inventory_agent = InventoryAgent()
purchase_agent = PurchaseAgent()
quality_agent = QualityAgent()
production_agent = ProductionAgent()
