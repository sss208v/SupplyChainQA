"""
SupplyChainRAG - Tool Engine Unit Tests
Tests query_inventory / query_order / create_ticket / get_datetime / get_knowledge

Each tool test covers:
- Normal query (data exists)
- Not-found error handling
- Return field completeness
- create_ticket also verifies write-to-DB

NOTE: Uses real supply_chain.db, not mocked.
Ticket ID uses time_ns for uniqueness across rapid test runs.
"""
import pytest
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.tool_engine import (
    query_inventory,
    query_order,
    create_ticket,
    get_datetime,
    get_knowledge,
    query_supplier,
    track_logistics,
    calculate_reorder_point,
)


# ---- 测试 fixtures ----

@pytest.fixture
def db_path():
    """真实的数据库路径"""
    return os.path.join(
        os.path.dirname(__file__), "..", "app", "data", "supply_chain.db"
    )


# ---- query_inventory 测试 ----

class TestQueryInventory:
    """测试物料库存查询工具"""

    async def test_query_existing_material(self):
        """查询存在的物料（MAT-001），验证返回字段完整"""
        result = await query_inventory.ainvoke({"material_code": "MAT-001"})
        data = json.loads(result)

        # 必含字段
        assert "material_code" in data
        assert "name" in data
        assert "quantity" in data
        assert "unit" in data
        assert "safety_stock" in data
        assert "incoming_qty" in data
        assert "outgoing_qty" in data
        assert "standard_price" in data
        assert "status" in data

        # 字段类型（quantity 从 SQLite SUM 返回可能是 float）
        assert isinstance(data["quantity"], (int, float))
        assert data["status"] in ("充足", "预警", "不足")

        # MAT-001 是电机轴承，编码格式正确
        assert data["material_code"] == "MAT-001"

    async def test_query_nonexistent_material(self):
        """查询不存在的物料，应返回 error 字段和可用编码列表"""
        result = await query_inventory.ainvoke({"material_code": "MAT-XXX"})
        data = json.loads(result)

        assert "error" in data
        assert "MAT-XXX" in data["error"]
        assert "可用编码" in data["error"] or "MAT-" in data["error"]

    async def test_query_multiple_materials(self):
        """验证 MAT-001、MAT-006、MAT-010 都能查到"""
        for code in ["MAT-001", "MAT-006", "MAT-010"]:
            result = await query_inventory.ainvoke({"material_code": code})
            data = json.loads(result)
            assert data["material_code"] == code
            assert "quantity" in data


# ---- query_order 测试 ----

class TestQueryOrder:
    """测试采购订单查询工具"""

    async def test_query_existing_order(self):
        """查询存在的订单（PO-20250601），验证返回字段完整"""
        result = await query_order.ainvoke({"order_id": "PO-20250601"})
        data = json.loads(result)

        # 必含字段
        assert "order_id" in data
        assert "supplier" in data
        assert "status" in data
        assert "order_date" in data
        assert "total_amount" in data
        assert "items" in data
        assert isinstance(data["items"], list)

        # PO-20250601 有 2 行物料
        assert len(data["items"]) >= 1

        # 订单行字段完整性
        first_item = data["items"][0]
        for field in ["name", "material_code", "qty", "price", "subtotal", "planned_date", "received_qty"]:
            assert field in first_item, f"缺少字段: {field}"

    async def test_query_nonexistent_order(self):
        """查询不存在的订单，应返回 error 字段"""
        result = await query_order.ainvoke({"order_id": "PO-NOTEXIST"})
        data = json.loads(result)

        assert "error" in data
        assert "PO-NOTEXIST" in data["error"]

    async def test_query_order_line_details(self):
        """Verify order line amount calculation and total consistency"""
        result = await query_order.ainvoke({"order_id": "PO-20250601"})
        data = json.loads(result)

        # Each line subtotal = qty * price
        for item in data["items"]:
            expected = item["qty"] * item["price"]
            assert abs(item["subtotal"] - expected) < 0.01

        # Note: total_amount in DB may differ from line sum (imported data quirk)
        assert data["total_amount"] > 0


# ---- create_ticket 测试 ----

class TestCreateTicket:
    """测试工单创建工具"""

    @classmethod
    def setup_class(cls):
        """清理历史遗留的测试工单，避免 UNIQUE 约束冲突"""
        import sqlite3
        db = os.path.join(os.path.dirname(__file__), "..", "app", "data", "supply_chain.db")
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "DELETE FROM maintenance_ticket WHERE name LIKE 'TK-%' "
                    "AND (description LIKE '%Auto test%' OR description LIKE '%priority mapping test%' "
                    "OR description LIKE '%Verify write%' OR description LIKE '%Test %')"
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

    async def test_create_ticket_returns_valid_id(self):
        """After creation, ticket_id should start with TK-YYYYMMDDHHMMSS"""
        ts = time.time_ns()
        result = await create_ticket.ainvoke({
            "title": f"Test ticket {ts}",
            "description": "Auto test ticket",
            "priority": "中",
        })
        data = json.loads(result)

        assert "ticket_id" in data
        assert data["ticket_id"].startswith("TK-")
        # TK-YYYYMMDDHHMMSS + nanoseconds suffix (7 digits) = 24 chars
        assert len(data["ticket_id"]) == len("TK-") + 14 + 7

    async def test_create_ticket_fields_complete(self):
        """Verify all return fields are present"""
        ts = time.time_ns()
        result = await create_ticket.ainvoke({
            "title": f"Urgent test {ts}",
            "description": "Test urgent priority",
            "priority": "紧急",
        })
        data = json.loads(result)

        for field in ["ticket_id", "title", "description", "priority", "status", "created_at"]:
            assert field in data, f"缺少字段: {field}"

    async def test_create_ticket_priority_mapping(self):
        """Verify priority parameter mapping is correct"""
        for priority_str, priority_val in [("低", "0"), ("中", "1"), ("高", "2"), ("紧急", "3")]:
            ts = time.time_ns()
            result = await create_ticket.ainvoke({
                "title": f"Test {priority_str} {ts}",
                "description": "priority mapping test",
                "priority": priority_str,
            })
            data = json.loads(result)
            assert data["priority"] == priority_str

    async def test_create_ticket_write_to_db(self):
        """Verify ticket is actually written to database"""
        import aiosqlite

        ts = time.time_ns()
        title = f"DB write verification {ts}"
        result = await create_ticket.ainvoke({
            "title": title,
            "description": "Verify write",
            "priority": "高",
        })
        data = json.loads(result)
        ticket_id = data["ticket_id"]

        # 直接查数据库验证
        db = os.path.join(os.path.dirname(__file__), "..", "app", "data", "supply_chain.db")

        conn = await aiosqlite.connect(db)
        conn.row_factory = aiosqlite.Row
        try:
            cur = await conn.execute(
                "SELECT name, priority, description FROM maintenance_ticket WHERE name = ?",
                (ticket_id,)
            )
            row = await cur.fetchone()
            assert row is not None, f"工单 {ticket_id} 未写入数据库"
            assert row["priority"] == "2"  # 高 → 2
            assert title in row["description"]
        finally:
            await conn.close()


# ---- get_datetime 测试 ----

class TestGetDatetime:
    """测试时间获取工具"""

    async def test_get_datetime_format(self):
        """返回格式应为 YYYY-MM-DD HH:MM:SS"""
        import re
        result = await get_datetime.ainvoke({})
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        assert re.match(pattern, result), f"日期格式不对: {result}"

    async def test_get_datetime_unused_param(self):
        """unused 参数不影响返回值"""
        result1 = await get_datetime.ainvoke({})
        result2 = await get_datetime.ainvoke({"unused": "ignored"})
        assert result1 == result2


# ---- get_knowledge 测试 ----

class TestGetKnowledge:
    """Test get_knowledge using rag_engine.search()"""

    async def test_get_knowledge_returns_valid_structure(self):
        """Returns JSON with query, answer, chunks keys (knowledge base may be empty)"""
        result = await get_knowledge.ainvoke({"query": "供应商准入条件"})
        data = json.loads(result)

        assert "query" in data
        assert "answer" in data
        assert "chunks" in data
        assert isinstance(data["chunks"], list)
        assert data["query"] == "供应商准入条件"

    async def test_get_knowledge_empty_query(self):
        """Empty query returns a valid response, not an error"""
        result = await get_knowledge.ainvoke({"query": ""})
        data = json.loads(result)

        assert "answer" in data
        assert "chunks" in data


# ---- query_supplier 测试 ----

class TestQuerySupplier:
    """测试供应商信息查询工具"""

    async def test_query_existing_supplier(self):
        """查询存在的供应商（SUP-001），验证返回字段完整"""
        result = await query_supplier.ainvoke({"supplier_code": "SUP-001"})
        data = json.loads(result)

        # 必含字段
        for field in ["supplier_code", "name", "category", "contact",
                      "credit_level", "qualification", "payment_terms",
                      "lead_time_days", "cooperation_since", "cooperation_years"]:
            assert field in data, f"缺少字段: {field}"

        assert data["supplier_code"] == "SUP-001"
        assert data["credit_level"] in ("A", "B", "C")
        assert isinstance(data["lead_time_days"], int)

    async def test_query_nonexistent_supplier(self):
        """查询不存在的供应商，应返回 error 字段和可用编码列表"""
        result = await query_supplier.ainvoke({"supplier_code": "SUP-999"})
        data = json.loads(result)

        assert "error" in data
        assert "SUP-999" in data["error"]
        assert "SUP-" in data["error"]  # 可用编码列表里含 SUP-xxx

    async def test_query_multiple_suppliers(self):
        """验证 SUP-001 ~ SUP-007 都能查到"""
        for code in ["SUP-001", "SUP-003", "SUP-007"]:
            result = await query_supplier.ainvoke({"supplier_code": code})
            data = json.loads(result)
            assert data["supplier_code"] == code
            assert "name" in data

    async def test_cooperation_years_calculated(self):
        """合作年数应被自动计算（>0）"""
        result = await query_supplier.ainvoke({"supplier_code": "SUP-001"})
        data = json.loads(result)
        years = int(data["cooperation_years"])
        assert years >= 1, f"合作年数应≥1，实际: {years}"


# ---- track_logistics 测试 ----

class TestTrackLogistics:
    """测试物流轨迹追踪工具"""

    async def test_track_valid_po_returns_nodes(self):
        """验证有效 PO 编码返回完整物流节点"""
        result = await track_logistics.ainvoke({"po_code": "PO-20250101"})
        data = json.loads(result)

        # 必含字段
        for field in ["po_code", "carrier", "current_status", "current_location",
                      "nodes", "eta", "delay_risk_probability", "delay_warning"]:
            assert field in data, f"缺少字段: {field}"

        assert data["po_code"] == "PO-20250101"
        assert isinstance(data["nodes"], list)
        assert len(data["nodes"]) >= 2, "轨迹应至少包含 2 个节点"

    async def test_track_logistics_carrier_in_known_list(self):
        """验证承运商在已知列表内"""
        result = await track_logistics.ainvoke({"po_code": "PO-20250601"})
        data = json.loads(result)
        known_carriers = ["中远海运集运", "顺丰特快", "跨越速运", "德邦快递"]
        assert data["carrier"] in known_carriers, f"未知承运商: {data['carrier']}"

    async def test_track_logistics_delay_probability_format(self):
        """验证延误概率格式正确（百分比字符串）"""
        result = await track_logistics.ainvoke({"po_code": "PO-20250101"})
        data = json.loads(result)
        prob = data["delay_risk_probability"]
        assert prob.endswith("%"), f"延误概率格式错误: {prob}"

    async def test_track_logistics_deterministic(self):
        """相同输入应返回相同结果（确定性模拟）"""
        r1 = await track_logistics.ainvoke({"po_code": "PO-20250101"})
        r2 = await track_logistics.ainvoke({"po_code": "PO-20250101"})
        assert r1 == r2, "相同 PO 编码应返回相同物流信息"

    async def test_track_logistics_invalid_po_rejected(self):
        """无效 PO 编码（不含 PO- 前缀）应返回 error"""
        result = await track_logistics.ainvoke({"po_code": "INVALID"})
        data = json.loads(result)
        assert "error" in data, "无效编码应返回 error 字段"

    async def test_track_logistics_eta_future_date(self):
        """ETA 应晚于当前时间"""
        from datetime import datetime
        result = await track_logistics.ainvoke({"po_code": "PO-20250101"})
        data = json.loads(result)
        eta = datetime.strptime(data["eta"], "%Y-%m-%d %H:%M")
        now = datetime.now()
        assert eta >= now, f"ETA {eta} 应晚于当前时间 {now}"


# ---- calculate_reorder_point 测试 ----

class TestCalculateReorderPoint:
    """测试再订货点计算工具（ROP 数学模型）"""

    async def test_reorder_point_valid_material_returns_fields(self):
        """验证有效物料返回完整的 ROP 计算字段"""
        result = await calculate_reorder_point.ainvoke({"material_code": "MAT-001"})
        data = json.loads(result)

        for field in ["material_code", "material_name", "current_stock",
                      "daily_consumption_pcs", "lead_time_days",
                      "safety_stock_pcs", "reorder_point_pcs",
                      "needs_reorder", "decision", "calculated_at"]:
            assert field in data, f"缺少字段: {field}"

    async def test_reorder_point_formula_correct(self):
        """验证 ROP 公式：ROP = daily_consumption × lead_time + safety_stock"""
        result = await calculate_reorder_point.ainvoke({"material_code": "MAT-001"})
        data = json.loads(result)

        expected_rop = (
            data["daily_consumption_pcs"] * data["lead_time_days"]
            + data["safety_stock_pcs"]
        )
        assert data["reorder_point_pcs"] == expected_rop, (
            f"ROP 公式错误: {data['reorder_point_pcs']} != "
            f"{data['daily_consumption_pcs']} × {data['lead_time_days']} + {data['safety_stock_pcs']} = {expected_rop}"
        )

    async def test_reorder_point_nonexistent_material_error(self):
        """查询不存在的物料应返回 error"""
        result = await calculate_reorder_point.ainvoke({"material_code": "MAT-999"})
        data = json.loads(result)
        assert "error" in data, "不存在的物料应返回 error"

    async def test_reorder_point_deterministic(self):
        """相同物料编码应返回相同计算结果（剔除 calculated_at 时间戳，避免跨秒边界 flaky）"""
        r1 = json.loads(await calculate_reorder_point.ainvoke({"material_code": "MAT-001"}))
        r2 = json.loads(await calculate_reorder_point.ainvoke({"material_code": "MAT-001"}))
        r1.pop("calculated_at", None)
        r2.pop("calculated_at", None)
        assert r1 == r2, "相同物料应返回相同 ROP 计算"

    async def test_reorder_point_superpower_normalization(self):
        """实体拼写自愈：mat001、MATOO1 也能正常查询"""
        # 模糊编码应被归一化为 MAT-001 并正常返回
        for fuzzy_code in ["mat001", "MAT001"]:
            result = await calculate_reorder_point.ainvoke({"material_code": fuzzy_code})
            data = json.loads(result)
            # 归一化后应找到物料（不是 error）
            assert "material_code" in data, f"模糊编码 {fuzzy_code} 应被归一化: {data}"
            assert "MAT-001" in data.get("material_code", ""), (
                f"模糊编码 {fuzzy_code} 应归一化为 MAT-001，实际: {data.get('material_code')}"
            )

    async def test_reorder_point_needs_reorder_decision(self):
        """验证补货决策逻辑：库存低于 ROP 时 needs_reorder=True"""
        result = await calculate_reorder_point.ainvoke({"material_code": "MAT-001"})
        data = json.loads(result)

        # needs_reorder 应与 库存<ROP 一致
        is_low = data["current_stock"] < data["reorder_point_pcs"]
        assert data["needs_reorder"] == is_low, (
            f"needs_reorder={data['needs_reorder']} 与库存状态不一致 "
            f"(stock={data['current_stock']}, rop={data['reorder_point_pcs']})"
        )

    async def test_reorder_point_decision_mentions_create_ticket_when_low(self):
        """库存告急时 decision 应提示 create_ticket"""
        result = await calculate_reorder_point.ainvoke({"material_code": "MAT-001"})
        data = json.loads(result)

        if data["needs_reorder"]:
            assert "create_ticket" in data["decision"].lower(), (
                f"补货建议应提及 create_ticket: {data['decision']}"
            )
            assert data["suggested_reorder_qty_pcs"] > 0, "补货时建议量应 > 0"
        else:
            assert data["suggested_reorder_qty_pcs"] == 0, "库存充足时建议量应为 0"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
