"""
图谱查询引擎 — 实体提取 + 3 个供应链场景 Cypher 模板

从用户查询中提取实体编码（物料/订单/供应商/工单），
匹配对应的预定义 Cypher 模板，调用 Neo4j 返回结构化结果。

设计决策：不用 LLM 生成 Cypher（不可控、有幻觉风险），
用模板化查询（确定性、可验证、面试时可解释）。
"""

import re
import logging

from app.core.neo4j_client import neo4j_client

logger = logging.getLogger(__name__)

# ---- 实体提取正则 ----
ENTITY_PATTERNS = {
    "material_codes": re.compile(r"MAT-\d{3,6}", re.IGNORECASE),
    "order_codes": re.compile(r"PO-\d{8,10}", re.IGNORECASE),
    "ticket_codes": re.compile(r"TK-\d{14,20}", re.IGNORECASE),
    "supplier_names": re.compile(
        r"供应商[：:]\s*([\u4e00-\u9fa5]+(?:科技|电子|集团|股份|制造|实业|供应链|物流|贸易|公司)?(?:有限公司)?)"
    ),
}


# ---- 3 个场景 Cypher 模板 ----

# 场景 1: 库存短缺评估 — 物料 → 在途 → 订单 → 供应商
CQL_INVENTORY_RISK = """
MATCH (m:Material {code: $material_code})
OPTIONAL MATCH (m)-[:HAS_MOVE]->(sm:StockMove)
OPTIONAL MATCH (m)<-[:FOR]-(ol:OrderLine)<-[:CONTAINS]-(po:PurchaseOrder)-[:FROM]->(s:Supplier)
RETURN
  m.code AS material_code,
  m.name AS material_name,
  m.qty_available AS stock,
  collect(DISTINCT {
    origin: sm.origin,
    qty: sm.qty,
    state: sm.state,
    expected: sm.expected_date
  }) AS in_transit,
  collect(DISTINCT {
    supplier: s.name,
    order_code: po.code,
    order_state: po.state,
    order_amount: po.amount_total,
    line_qty: ol.qty,
    line_price: ol.price_unit,
    date_planned: ol.date_planned
  }) AS on_order
"""

# 场景 2: 质量追溯 — 物料 → 工单 + 上游供应商
CQL_QUALITY_TRACE = """
MATCH (m:Material {code: $material_code})
OPTIONAL MATCH (m)-[:RELATED_TO]->(t:Ticket)
OPTIONAL MATCH (m)<-[:FOR]-(ol:OrderLine)<-[:CONTAINS]-(po:PurchaseOrder)-[:FROM]->(s:Supplier)
RETURN
  m.code AS material_code,
  m.name AS material_name,
  collect(DISTINCT {
    code: t.code,
    priority: t.priority
  }) AS tickets,
  collect(DISTINCT {
    supplier: s.name,
    order_code: po.code,
    batch_qty: ol.qty,
    date_planned: ol.date_planned
  }) AS upstream_suppliers
"""

# 场景 3: 供应商影响分析 — 供应商 → 所有物料 → 关联工单
CQL_SUPPLIER_IMPACT = """
MATCH (s:Supplier {name: $supplier_name})<-[:FROM]-(po:PurchaseOrder)
MATCH (po)-[:CONTAINS]->(ol:OrderLine)-[:FOR]->(m:Material)
OPTIONAL MATCH (m)-[:RELATED_TO]->(t:Ticket)
RETURN
  s.name AS supplier,
  collect(DISTINCT {
    code: m.code,
    name: m.name,
    stock: m.qty_available,
    order_code: po.code,
    order_qty: ol.qty
  }) AS supplied_materials,
  collect(DISTINCT {
    material_code: m.code,
    ticket_code: t.code,
    ticket_priority: t.priority
  }) AS affected_tickets
"""

def extract_entities(query: str) -> dict:
    """从查询文本中提取供应链实体编码"""
    entities = {}
    for key, pattern in ENTITY_PATTERNS.items():
        matches = list(set(pattern.findall(query)))
        if matches:
            entities[key] = matches
    return entities


class GraphEngine:
    """图谱查询引擎"""

    async def query(self, query: str) -> dict:
        """
        执行图谱查询。

        Returns:
            {
                "entities": {...},
                "pattern": "inventory_risk" | "quality_trace" | "supplier_impact" | None,
                "rows": [...],
                "error": str | None,
            }
        """
        entities = extract_entities(query)
        logger.info(
            "[GraphEngine] 实体提取: query='%s' entities=%s", query[:80], entities
        )

        if not entities:
            return {
                "entities": {},
                "pattern": None,
                "rows": [],
                "error": None,
                "reason": "未提取到任何供应链实体编码",
            }

        if not neo4j_client.is_connected:
            return {
                "entities": entities,
                "pattern": None,
                "rows": [],
                "error": "Neo4j 未连接",
            }

        results = []
        pattern = None

        async with neo4j_client.get_session() as session:
            # 场景 1 & 2: 有物料编码 → 库存 + 质量追溯
            for mat_code in entities.get("material_codes", []):
                # 1. 库存短缺评估
                try:
                    r = await session.run(
                        CQL_INVENTORY_RISK, material_code=mat_code
                    )
                    record = await r.single()
                    if record:
                        row = dict(record)
                        row["_pattern"] = "inventory_risk"
                        results.append(row)
                        pattern = "inventory_risk"
                except Exception as e:
                    logger.warning(
                        "[GraphEngine] inventory_risk 查询失败: %s", e
                    )

                # 2. 质量追溯
                try:
                    r = await session.run(
                        CQL_QUALITY_TRACE, material_code=mat_code
                    )
                    record = await r.single()
                    if record:
                        row = dict(record)
                        row["_pattern"] = "quality_trace"
                        results.append(row)
                        if not pattern:
                            pattern = "quality_trace"
                except Exception as e:
                    logger.warning(
                        "[GraphEngine] quality_trace 查询失败: %s", e
                    )

            # 场景 3: 供应商影响
            for sup_name in entities.get("supplier_names", []):
                try:
                    r = await session.run(
                        CQL_SUPPLIER_IMPACT, supplier_name=sup_name
                    )
                    record = await r.single()
                    if record:
                        row = dict(record)
                        row["_pattern"] = "supplier_impact"
                        results.append(row)
                        if not pattern:
                            pattern = "supplier_impact"
                except Exception as e:
                    logger.warning(
                        "[GraphEngine] supplier_impact 查询失败: %s", e
                    )

        return {
            "entities": entities,
            "pattern": pattern,
            "rows": results,
            "error": None,
        }

    @staticmethod
    def format_results(graph_result: dict) -> str:
        """将图谱查询结果格式化为 LLM 可读的文本上下文"""
        rows = graph_result.get("rows", [])
        if not rows:
            return ""

        parts = []
        for row in rows:
            # 按行各自的 _pattern 选择模板，而非全局 pattern
            row_pattern = row.get("_pattern", graph_result.get("pattern", ""))
            if row_pattern == "inventory_risk":
                parts.append(f"物料 {row.get('material_code')} ({row.get('material_name')})")
                parts.append(f"  现货库存: {row.get('stock')}")
                for sm in row.get("in_transit", []) or []:
                    if isinstance(sm, dict):
                        parts.append(f"  在途: {sm.get('origin')} x{sm.get('qty')} 预计{sm.get('expected')}")
                for po in row.get("on_order", []) or []:
                    if isinstance(po, dict):
                        parts.append(
                            f"  采购: {po.get('supplier')} {po.get('order_code')} "
                            f"x{po.get('line_qty')} 预计{po.get('date_planned')}"
                        )

            elif row_pattern == "quality_trace":
                parts.append(f"物料 {row.get('material_code')} ({row.get('material_name')})")
                for t in row.get("tickets", []) or []:
                    if isinstance(t, dict):
                        parts.append(f"  工单: {t.get('code')} (优先级={t.get('priority')})")
                for sup in row.get("upstream_suppliers", []) or []:
                    if isinstance(sup, dict):
                        parts.append(
                            f"  上游: {sup.get('supplier')} {sup.get('order_code')} "
                            f"x{sup.get('batch_qty')}"
                        )

            elif row_pattern == "supplier_impact":
                parts.append(f"供应商 {row.get('supplier')}")
                for mat in row.get("supplied_materials", []) or []:
                    if isinstance(mat, dict):
                        parts.append(
                            f"  物料: {mat.get('code')} {mat.get('name')} "
                            f"库存={mat.get('stock')} 订单={mat.get('order_code')}"
                        )
                for t in row.get("affected_tickets", []) or []:
                    if isinstance(t, dict) and t.get("ticket_code"):
                        parts.append(f"  关联工单: {t.get('ticket_code')} (优先级={t.get('ticket_priority')})")

        return "\n".join(parts) if parts else ""


# 全局单例
graph_engine = GraphEngine()
