"""质量 Agent — 负责知识库检索（质量标准/规范）、质量异常工单"""
from app.agents.domain_agent import DomainAgent


class QualityAgent(DomainAgent):
    TOOL_NAMES = ["get_knowledge", "create_ticket"]


quality_agent = QualityAgent()
