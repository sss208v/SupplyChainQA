"""工具模块数据库操作完善测试

覆盖 plan 要求：
- query_ticket：写入后查询成功 / 不存在返回最近工单 / 空输入
- query_stock_move：有在途记录返回 / 无记录提示
- 写读闭环与缓存一致性：create_ticket 后 query_ticket 立即可读（L3 tool 缓存已失效）
- 安全库存口径：query_inventory 与 calculate_reorder_point 对同一物料一致
- ROLE_TOOLS 完整性：无死工具、WRITE_TOOLS 单一来源、业务映射合理性
- 权限判定：employee 无写工具权限、可调只读工具

测试使用临时 SQLite 库（monkeypatch _DATA_DB），不污染真实 supply_chain.db。
"""
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.tool_engine as te


def _create_temp_db(tmp_path) -> str:
    """创建临时 SQLite 库（含工具所需表与种子数据），返回路径"""
    db_path = str(tmp_path / "test_tool.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE maintenance_ticket (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, origin TEXT,
            user_id INTEGER DEFAULT 1, priority TEXT DEFAULT '1', categ_id INTEGER,
            stage_id INTEGER DEFAULT 0, description TEXT,
            create_date TEXT, write_date TEXT, date_deadline TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE product_product (
            id INTEGER PRIMARY KEY, default_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            categ_id INTEGER, uom_id INTEGER, standard_price REAL DEFAULT 0,
            qty_available REAL DEFAULT 0, virtual_available REAL DEFAULT 0,
            incoming_qty REAL DEFAULT 0, outgoing_qty REAL DEFAULT 0,
            create_date TEXT, write_date TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE stock_move (
            id INTEGER PRIMARY KEY, origin TEXT, product_id INTEGER,
            product_uom_qty REAL DEFAULT 0, location_id INTEGER, location_dest_id INTEGER,
            state TEXT DEFAULT 'draft', date_expected TEXT, date_done TEXT, reference TEXT
        )
    """)
    cur.execute(
        "INSERT INTO product_product (default_code, name, qty_available, incoming_qty, outgoing_qty) "
        "VALUES ('MAT-001', '电机轴承 6205-2RS', 1500, 0, 0)"
    )
    cur.execute(
        "INSERT INTO stock_move (origin, product_id, product_uom_qty, location_id, "
        "location_dest_id, state, date_expected, reference) "
        "VALUES ('PO-20250602', 1, 500, 8, 9, 'assigned', '2026-08-15', 'IN/2025/001')"
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def tool_db(tmp_path, monkeypatch):
    """将工具指向临时库并 mock L3 缓存，返回临时库路径"""
    db_path = _create_temp_db(tmp_path)
    monkeypatch.setattr(te, "_DATA_DB", db_path)

    async def _passthrough(namespace, key, ttl, loader, cache_if=None):
        return await loader()

    mock_cm = MagicMock()
    mock_cm.l3_get_or_set = AsyncMock(side_effect=_passthrough)
    mock_cm.l3_invalidate = AsyncMock(return_value=1)
    monkeypatch.setattr("app.core.cache_manager.cache_manager", mock_cm)
    return db_path


# ============================================================
# query_ticket
# ============================================================

class TestQueryTicket:
    async def test_query_after_create_roundtrip(self, tool_db):
        """写读闭环：create_ticket 创建后 query_ticket 立即可读"""
        created = await te.create_ticket.ainvoke({
            "title": "库存不足", "description": "MAT-001 库存低于再订货点", "priority": "高",
        })
        created_data = json.loads(created)
        ticket_id = created_data["ticket_id"]

        result = await te.query_ticket.ainvoke({"ticket_id": ticket_id})
        data = json.loads(result)
        assert data["ticket_id"] == ticket_id
        assert data["priority"] == "高"
        assert data["status"] == "待处理"
        assert "库存不足" in data["title"]

    async def test_not_found_returns_recent_hint(self, tool_db):
        result = await te.query_ticket.ainvoke({"ticket_id": "TK-0000000000"})
        data = json.loads(result)
        assert "error" in data
        assert "最近工单" in data["error"]

    async def test_empty_input_rejected(self, tool_db):
        result = await te.query_ticket.ainvoke({"ticket_id": "   "})
        data = json.loads(result)
        assert "error" in data


# ============================================================
# query_stock_move
# ============================================================

class TestQueryStockMove:
    async def test_existing_po_returns_moves(self, tool_db):
        result = await te.query_stock_move.ainvoke({"po_code": "PO-20250602"})
        data = json.loads(result)
        assert data["count"] == 1
        assert data["moves"][0]["material_code"] == "MAT-001"
        assert data["moves"][0]["state"] == "已分配"
        assert data["moves"][0]["reference"] == "IN/2025/001"

    async def test_no_record_returns_hint(self, tool_db):
        result = await te.query_stock_move.ainvoke({"po_code": "PO-99999999"})
        data = json.loads(result)
        assert "error" in data
        assert "在途" in data["error"]

    async def test_empty_input_rejected(self, tool_db):
        result = await te.query_stock_move.ainvoke({"po_code": ""})
        data = json.loads(result)
        assert "error" in data


# ============================================================
# 写读闭环与缓存失效（真实 L3 缓存交互模拟）
# ============================================================

class TestWriteReadCacheConsistency:
    async def test_create_invalidates_then_read_hits_db(self, tool_db, monkeypatch):
        """create_ticket 写后失效 tool 缓存；随后 query_ticket 走真实回源而非旧缓存"""
        invalidated = []

        async def fake_invalidate(namespace):
            invalidated.append(namespace)

        # 第一次缓存调用捕获 loader 结果
        cache_store = {}

        async def fake_get_or_set(namespace, key, ttl, loader, cache_if=None):
            if key in cache_store:
                return cache_store[key]
            value = await loader()
            if cache_if is None or cache_if(value):
                cache_store[key] = value
            return value

        mock_cm = MagicMock()
        mock_cm.l3_get_or_set = AsyncMock(side_effect=fake_get_or_set)
        mock_cm.l3_invalidate = AsyncMock(side_effect=fake_invalidate)
        monkeypatch.setattr("app.core.cache_manager.cache_manager", mock_cm)

        # 写工单 → 应触发 tool 命名空间失效
        created = json.loads(await te.create_ticket.ainvoke({
            "title": "闭环验证", "description": "写后读", "priority": "中",
        }))
        assert "tool" in invalidated

        # 读工单 → 走回源（失效后无旧缓存）
        read = json.loads(await te.query_ticket.ainvoke({"ticket_id": created["ticket_id"]}))
        assert read["ticket_id"] == created["ticket_id"]


# ============================================================
# 安全库存口径一致性
# ============================================================

class TestSafetyStockConsistency:
    def test_shared_function(self):
        assert te._safety_stock_for(1500) == 150
        assert te._safety_stock_for(100) == 50  # 下限 50
        assert te._safety_stock_for(0) == 50

    async def test_inventory_and_rop_agree_on_same_material(self, tool_db, monkeypatch):
        """MAT-001 上 query_inventory 与 calculate_reorder_point 的安全库存一致"""
        inv = json.loads(await te.query_inventory.ainvoke({"material_code": "MAT-001"}))
        rop = json.loads(await te.calculate_reorder_point.ainvoke({"material_code": "MAT-001"}))
        assert inv["safety_stock"] == rop["safety_stock_pcs"]


# ============================================================
# ROLE_TOOLS 完整性矩阵
# ============================================================

class TestRoleToolsCompleteness:
    def test_no_dead_tools(self):
        """TOOL_REGISTRY 每个工具至少被一个角色映射（无权限死工具）"""
        from app.api.tool import ROLE_TOOLS
        registered = set(te.TOOL_REGISTRY)
        mapped = set().union(*ROLE_TOOLS.values())
        assert registered <= mapped, f"无权限映射的死工具: {registered - mapped}"

    def test_write_tools_single_source(self):
        """WRITE_TOOLS 单一来源：handlers 与 tool.py 引用同一对象"""
        from app.api.handlers.tool_call import WRITE_TOOLS as HANDLER_WRITE_TOOLS
        from app.api.tool import WRITE_TOOLS
        assert HANDLER_WRITE_TOOLS is WRITE_TOOLS
        assert WRITE_TOOLS == {"create_ticket"}

    def test_business_mapping_rationale(self):
        """业务合理性映射：warehouse 可算再订货点、quality 可查供应商、finance 可查订单/库存"""
        from app.api.tool import ROLE_TOOLS
        assert "calculate_reorder_point" in ROLE_TOOLS["warehouse"]
        assert "query_supplier" in ROLE_TOOLS["quality"]
        assert "query_order" in ROLE_TOOLS["finance"]
        assert "query_inventory" in ROLE_TOOLS["finance"]
        # 新工具映射到相关业务部门
        assert "query_ticket" in ROLE_TOOLS["finance"]
        assert "query_stock_move" in ROLE_TOOLS["logistics"]
        # code_interpreter 仅 admin（风险工具最小化）
        for role, tools in ROLE_TOOLS.items():
            if role != "admin":
                assert "code_interpreter" not in tools

    def test_write_level_matrix(self):
        """写工具需 manager+；只读工具所有级别可用"""
        from app.api.tool import _is_tool_allowed
        assert _is_tool_allowed("create_ticket", "purchase", "employee") is False
        assert _is_tool_allowed("create_ticket", "purchase", "manager") is True
        assert _is_tool_allowed("query_ticket", "purchase", "employee") is True
        assert _is_tool_allowed("query_stock_move", "purchase", "employee") is True
        assert _is_tool_allowed("code_interpreter", "admin", "admin") is True
        assert _is_tool_allowed("code_interpreter", "purchase", "admin") is False
