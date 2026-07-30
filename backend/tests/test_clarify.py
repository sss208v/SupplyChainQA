"""Clarify 澄清提问模块 — pytest 测试"""
import pytest

from app.core.clarify import ClarifyResult, check_needs_clarification


# ---------------------------------------------------------------------------
# check_needs_clarification — query_inventory
# ---------------------------------------------------------------------------

class TestQueryInventoryClarification:
    def test_missing_material_code_returns_clarify(self):
        result = check_needs_clarification("查一下库存", "query_inventory")
        assert result is not None
        assert result.needs_clarification is True
        assert "material_code" in result.missing_params
        assert result.tool_name == "query_inventory"

    def test_mat_code_present_no_clarify(self):
        result = check_needs_clarification("查一下MAT-001的库存", "query_inventory")
        assert result is None

    def test_material_name_present_no_clarify(self):
        for name in ("轴承", "液压油", "螺栓", "传送带", "PLC"):
            result = check_needs_clarification(f"查{name}库存", "query_inventory")
            assert result is None, f"Expected no clarification for '{name}'"

    def test_material_code_format_no_clarify(self):
        result = check_needs_clarification("物料编码 ABC123 的库存", "query_inventory")
        assert result is None

    def test_clarify_question_content(self):
        result = check_needs_clarification("库存怎么样", "query_inventory")
        assert result is not None
        assert "物料" in result.question


# ---------------------------------------------------------------------------
# check_needs_clarification — query_order
# ---------------------------------------------------------------------------

class TestQueryOrderClarification:
    def test_missing_order_id_returns_clarify(self):
        result = check_needs_clarification("订单情况", "query_order")
        # query_order without order_id triggers clarification
        assert result is not None
        assert result.needs_clarification is True
    def test_po_number_present_no_clarify(self):
        result = check_needs_clarification("查一下PO-20250101的状态", "query_order")
        assert result is None

    def test_order_keyword_present_no_clarify(self):
        result = check_needs_clarification("采购单号 XYZ 的状态", "query_order")
        assert result is None


# ---------------------------------------------------------------------------
# check_needs_clarification — create_ticket
# ---------------------------------------------------------------------------

class TestCreateTicketClarification:
    def test_create_ticket_always_none(self):
        """create_ticket 的 clarify_question 为 None，永远返回 None"""
        result = check_needs_clarification("帮我创建工单", "create_ticket")
        assert result is None

    def test_create_ticket_empty_query(self):
        result = check_needs_clarification("", "create_ticket")
        assert result is None


# ---------------------------------------------------------------------------
# Unknown tool / edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_tool_returns_none(self):
        result = check_needs_clarification("anything", "nonexistent_tool")
        assert result is None

    def test_empty_query_inventory(self):
        result = check_needs_clarification("", "query_inventory")
        assert result is not None
        assert result.needs_clarification is True

    def test_empty_query_order(self):
        result = check_needs_clarification("", "query_order")
        assert result is not None
        assert result.needs_clarification is True

    def test_very_long_query_missing_param(self):
        long_query = "请帮我查一下" * 100
        result = check_needs_clarification(long_query, "query_inventory")
        assert result is not None
        assert result.needs_clarification is True

    def test_special_characters_query(self):
        result = check_needs_clarification("@#$%^&*()", "query_inventory")
        assert result is not None
        assert result.needs_clarification is True

    def test_whitespace_only_query(self):
        result = check_needs_clarification("   ", "query_inventory")
        assert result is not None
        assert result.needs_clarification is True

    def test_clarify_result_fields(self):
        result = check_needs_clarification("查库存", "query_inventory")
        assert result is not None
        assert isinstance(result, ClarifyResult)
        assert isinstance(result.missing_params, list)
        assert isinstance(result.question, str)
        assert isinstance(result.tool_name, str)
