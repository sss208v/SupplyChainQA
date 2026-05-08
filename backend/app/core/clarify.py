"""
SmartQA - 澄清提问模块

当用户查询缺少必要参数时，主动询问而非猜测或失败。

Hermes Agent 的 clarify 工具：不确定时直接问用户，比猜测更可靠。
本模块实现轻量级的参数检查，在工具调用前拦截不完整的请求。

这和 Hermes Agent 的 clarify 工具理念一致——不确定就问，比猜错更好。"
"""
import logging
import re
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ClarifyResult:
    """澄清结果"""
    needs_clarification: bool
    question: str  # 要问用户的问题
    missing_params: list[str]  # 缺少的参数
    tool_name: str  # 目标工具


# 各工具的必需参数和对应的提问模板
TOOL_PARAM_RULES = {
    "query_inventory": {
        "required": ["material_code"],
        "patterns": {
            "material_code": [
                r"MAT-\d+",           # MAT-001 格式
                r"物料[编代]码\s*\S+",  # 物料编码xxx
                r"轴承|液压油|螺栓|传送带|PLC",  # 具体物料名称
            ],
        },
        "clarify_question": "请问您想查询哪个物料的库存？可以提供物料编码（如 MAT-001）或物料名称。",
    },
    "query_order": {
        "required": ["order_id"],
        "patterns": {
            "order_id": [
                r"PO-\d+",            # PO-20250101 格式
                r"采购[单订]号?\s*\S+", # 采购单号xxx
            ],
        },
        "clarify_question": "请问您想查询哪个采购单的状态？请提供采购单号（如 PO-20250101）。",
    },
    "create_ticket": {
        "required": ["title"],
        "patterns": {
            "title": [
                r"工单[标题目]?\s*\S+",
                r"[创建建][\s]*工单",
                r"补货",
                r"申请",
            ],
        },
        "clarify_question": None,  # create_ticket 通常从上下文推断，不需要额外询问
    },
}


def check_needs_clarification(query: str, tool_name: str) -> Optional[ClarifyResult]:
    """检查查询是否需要澄清

    Args:
        query: 用户查询
        tool_name: 目标工具名

    Returns:
        ClarifyResult 如果需要澄清，None 如果参数充足
    """
    rules = TOOL_PARAM_RULES.get(tool_name)
    if not rules:
        return None  # 没有规则的工具不需要澄清

    # 检查每个必需参数
    missing = []
    for param in rules["required"]:
        patterns = rules["patterns"].get(param, [])
        if patterns:
            # 有模式匹配规则：检查查询中是否包含匹配
            matched = any(re.search(p, query) for p in patterns)
            if not matched:
                missing.append(param)
        # 没有模式的参数（如 title）通常可以从上下文推断

    if missing and rules["clarify_question"]:
        logger.info(f"[Clarify] query='{query}' tool={tool_name} missing={missing}")
        return ClarifyResult(
            needs_clarification=True,
            question=rules["clarify_question"],
            missing_params=missing,
            tool_name=tool_name,
        )

    return None
