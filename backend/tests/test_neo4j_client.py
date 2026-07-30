"""Neo4j 客户端单元测试 — 之前 0 覆盖"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestNeo4jClientConnection:
    def test_init_state(self):
        """Neo4jClient 初始为未连接状态"""
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        assert client.is_connected is False
        assert client._driver is None

    @pytest.mark.asyncio
    async def test_connect_success_returns_true(self, monkeypatch):
        """connect 成功应返回 True 并设 _connected=True"""
        from app.core import neo4j_client as nc_mod

        fake_driver = MagicMock()
        fake_driver.verify_connectivity = AsyncMock()
        monkeypatch.setattr(nc_mod, "AsyncGraphDatabase", MagicMock(driver=MagicMock(return_value=fake_driver)))

        client = nc_mod.Neo4jClient()
        result = await client.connect()
        assert result is True
        assert client.is_connected is True
        assert client._driver is fake_driver

    @pytest.mark.asyncio
    async def test_connect_failure_returns_false_no_raise(self, monkeypatch):
        """connect 失败应返回 False 且不抛错（比 Redis 优雅）"""
        from app.core import neo4j_client as nc_mod

        fake_driver = MagicMock()
        fake_driver.verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("neo4j down"))
        monkeypatch.setattr(nc_mod, "AsyncGraphDatabase", MagicMock(driver=MagicMock(return_value=fake_driver)))

        client = nc_mod.Neo4jClient()
        result = await client.connect()
        assert result is False
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_closes_driver(self):
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        fake_driver = MagicMock()
        fake_driver.close = AsyncMock()
        client._driver = fake_driver
        client._connected = True
        await client.disconnect()
        fake_driver.close.assert_awaited_once()
        assert client._driver is None
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_noop_when_not_connected(self):
        """未连接时 disconnect 不抛错"""
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        await client.disconnect()  # 不抛错
        assert client._driver is None

    def test_get_session_raises_when_not_connected(self):
        """未连接时 get_session 应抛 RuntimeError"""
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        with pytest.raises(RuntimeError, match="Neo4j 未连接"):
            client.get_session()

    def test_get_session_returns_driver_session_when_connected(self):
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        fake_driver = MagicMock()
        fake_session = MagicMock()
        fake_driver.session.return_value = fake_session
        client._driver = fake_driver
        client._connected = True

        session = client.get_session()
        assert session is fake_session

    @pytest.mark.asyncio
    async def test_health_when_not_connected(self):
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        result = await client.health()
        assert result == {"connected": False}

    @pytest.mark.asyncio
    async def test_health_when_connected_success(self):
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        fake_driver = MagicMock()
        fake_driver.verify_connectivity = AsyncMock()
        client._driver = fake_driver
        client._connected = True

        result = await client.health()
        assert result == {"connected": True}

    @pytest.mark.asyncio
    async def test_health_when_connected_failure(self):
        """连接中但 verify_connectivity 失败 → connected: False"""
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        fake_driver = MagicMock()
        fake_driver.verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("session expired"))
        client._driver = fake_driver
        client._connected = True  # 标记为连接但实际已断

        result = await client.health()
        assert result == {"connected": False}


class TestNeo4jClientSync:
    @pytest.mark.asyncio
    async def test_sync_when_not_connected(self):
        """未连接时 sync_from_sqlite 返回 synced=False"""
        from app.core.neo4j_client import Neo4jClient
        client = Neo4jClient()
        result = await client.sync_from_sqlite()
        assert result["synced"] is False
        assert "未连接" in result.get("reason", "")


class TestNeo4jNormalizeEntity:
    def test_normalize_trim_and_upper(self):
        """_normalize_entity 应去首尾空格 + 全部大写"""
        from app.core.neo4j_client import Neo4jClient
        assert Neo4jClient._normalize_entity("  mat-001  ") == "MAT-001"

    def test_normalize_letter_o_to_zero(self):
        """MAT-OO1 / MATOO1 / MAT OO1 → MAT-001（字母 O 纠正为 0）"""
        from app.core.neo4j_client import Neo4jClient
        assert Neo4jClient._normalize_entity("MAT-OO1") == "MAT-001"
        assert Neo4jClient._normalize_entity("MATOO1") == "MAT-001"
        assert Neo4jClient._normalize_entity("mat oo1") == "MAT-001"

    def test_normalize_add_missing_hyphen(self):
        """MAT001 → MAT-001（补充缺失的连字符）"""
        from app.core.neo4j_client import Neo4jClient
        assert Neo4jClient._normalize_entity("MAT001") == "MAT-001"
        assert Neo4jClient._normalize_entity("PO20250101") == "PO-20250101"
        assert Neo4jClient._normalize_entity("SUP123") == "SUP-123"

    def test_normalize_po_and_sup(self):
        """PO/SUP 前缀同样被支持"""
        from app.core.neo4j_client import Neo4jClient
        assert Neo4jClient._normalize_entity("po-001") == "PO-001"
        assert Neo4jClient._normalize_entity("sup-001") == "SUP-001"

    def test_normalize_chinese_passthrough(self):
        """非 MAT/PO/SUP 前缀的字符串保持原样（仅 strip+upper）"""
        from app.core.neo4j_client import Neo4jClient
        result = Neo4jClient._normalize_entity("  供应商  ")
        # 中文没匹配任何规则 → 返回原字符串
        assert result.strip() == "供应商" or "供应商" in result

    def test_normalize_already_normalized_unchanged(self):
        """已经标准化的字符串应保持不变"""
        from app.core.neo4j_client import Neo4jClient
        assert Neo4jClient._normalize_entity("MAT-001") == "MAT-001"


# ---- 必要导入 ----
try:
    from neo4j.exceptions import ServiceUnavailable
except ImportError:
    # 兜底：neo4j 包未装时用一个类占位
    class ServiceUnavailable(Exception):
        pass
