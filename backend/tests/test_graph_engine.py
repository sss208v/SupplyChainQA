"""
test_graph_engine.py — 图谱查询引擎单元测试

覆盖：实体提取 / 图查询分类 / Cypher 模板（需要 Neo4j 在线）
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core.graph_engine import (
    extract_entities,
    classify_graph_query,
    ENTITY_PATTERNS,
)


class TestEntityExtraction:
    """实体提取 — 正则匹配"""

    def test_extract_material_code(self):
        entities = extract_entities("MAT-001 库存不够怎么办")
        assert "MAT-001" in entities.get("material_codes", [])

    def test_extract_multiple_materials(self):
        entities = extract_entities("查 MAT-001 和 MAT-002 的库存")
        codes = entities.get("material_codes", [])
        assert "MAT-001" in codes
        assert "MAT-002" in codes

    def test_extract_order_code(self):
        entities = extract_entities("PO-20250601 什么时候到货")
        assert "PO-20250601" in entities.get("order_codes", [])

    def test_extract_ticket_code(self):
        entities = extract_entities("TK-20250601120000 处理进度")
        assert entities.get("ticket_codes")

    def test_no_entities(self):
        entities = extract_entities("什么是安全库存")
        assert not entities  # 无实体编码

    def test_case_insensitive(self):
        entities = extract_entities("mat-001 缺货")
        assert "mat-001" in entities.get("material_codes", [])


class TestGraphQueryClassification:
    """图谱查询分类 — 判断查询是否应走图检索"""

    def test_graph_query_with_entity_and_keyword(self):
        """含实体编码 + 关系词 → 图检索"""
        assert classify_graph_query(
            "MAT-001 缺货会影响哪些物料",
            {"material_codes": ["MAT-001"]},
        )

    def test_graph_query_supplier_impact(self):
        assert classify_graph_query(
            "PO-001 延迟影响什么",
            {"order_codes": ["PO-001"]},
        )

    def test_not_graph_query_concept_only(self):
        """纯概念问题 → 不走图检索"""
        assert not classify_graph_query(
            "什么是安全库存",
            {},
        )

    def test_not_graph_query_entity_no_keyword(self):
        """有实体但无关系词 → 不走图检索（可能走工具调用）"""
        assert not classify_graph_query(
            "查 MAT-001 库存",
            {"material_codes": ["MAT-001"]},
        )


class TestCypherTemplates:
    """Cypher 模板 — 语法验证（需 Neo4j 在线）"""

    @pytest.mark.integration
    async def test_inventory_risk_cypher(self):
        """场景 1: 库存短缺评估 — Cypher 语法有效"""
        from app.core.neo4j_client import neo4j_client
        from app.core.graph_engine import CQL_INVENTORY_RISK

        if not neo4j_client.is_connected:
            pytest.skip("Neo4j 未连接")

        async with neo4j_client._driver.session() as session:
            # EXPLAIN 验证语法，不实际执行
            r = await session.run(
                "EXPLAIN " + CQL_INVENTORY_RISK,
                material_code="MAT-001",
            )
            record = await r.single()
            assert record is not None

    @pytest.mark.integration
    async def test_quality_trace_cypher(self):
        """场景 2: 质量追溯 — Cypher 语法有效"""
        from app.core.neo4j_client import neo4j_client
        from app.core.graph_engine import CQL_QUALITY_TRACE

        if not neo4j_client.is_connected:
            pytest.skip("Neo4j 未连接")

        async with neo4j_client._driver.session() as session:
            r = await session.run(
                "EXPLAIN " + CQL_QUALITY_TRACE,
                material_code="MAT-001",
            )
            record = await r.single()
            assert record is not None

    @pytest.mark.integration
    async def test_supplier_impact_cypher(self):
        """场景 3: 供应商影响 — Cypher 语法有效"""
        from app.core.neo4j_client import neo4j_client
        from app.core.graph_engine import CQL_SUPPLIER_IMPACT

        if not neo4j_client.is_connected:
            pytest.skip("Neo4j 未连接")

        async with neo4j_client._driver.session() as session:
            r = await session.run(
                "EXPLAIN " + CQL_SUPPLIER_IMPACT,
                supplier_name="深圳赛意法微电子有限公司",
            )
            record = await r.single()
            assert record is not None
