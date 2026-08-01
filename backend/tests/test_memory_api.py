"""三层记忆体系 API 测试

复用 conftest 的 client fixture（内存 SQLite + fake Redis + patch 认证），
覆盖：画像读写、部门记忆角色隔离、术语表 admin 权限、未登录 401。

注意：memory_service 单例动态绑定 redis_client.redis_manager（被 client
fixture patch 为 fake），每个测试自动重建，无需手动 reset。
"""
import pytest

from app.core.memory_service import reset_memory_service


@pytest.fixture(autouse=True)
def _reset_memory_service():
    """每个测试前重置三层记忆单例，确保绑定当前 fake redis"""
    reset_memory_service()
    yield
    reset_memory_service()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 用户层：画像
# ============================================================

class TestProfileAPI:
    async def test_profile_empty_initial(self, client, seed_user):
        resp = await client.get("/api/v1/memory/profile", headers=_auth(seed_user["token"]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"]["preferences"] == []

    async def test_add_and_read_preference(self, client, seed_user):
        resp = await client.post(
            "/api/v1/memory/profile/preferences",
            json={"preference": "偏好简洁回答"},
            headers=_auth(seed_user["token"]),
        )
        assert resp.status_code == 200

        resp = await client.get("/api/v1/memory/profile", headers=_auth(seed_user["token"]))
        assert resp.status_code == 200
        profile = resp.json()["profile"]
        assert "偏好简洁回答" in profile["preferences"]

    async def test_profile_requires_login(self, client):
        resp = await client.get("/api/v1/memory/profile")
        assert resp.status_code == 401


# ============================================================
# 部门层：部门记忆（角色隔离）
# ============================================================

class TestDeptMemoryAPI:
    async def test_add_and_read_own_dept(self, client, seed_user):
        resp = await client.post(
            "/api/v1/memory/dept/notes",
            json={"content": "供应商准入需双人复核"},
            headers=_auth(seed_user["token"]),
        )
        assert resp.status_code == 200

        resp = await client.get("/api/v1/memory/dept", headers=_auth(seed_user["token"]))
        assert resp.status_code == 200
        notes = resp.json()["notes"]
        assert len(notes) == 1
        assert notes[0]["content"] == "供应商准入需双人复核"

    async def test_cross_dept_request_downgraded(self, client, seed_user):
        """非 admin 请求其他部门：服务端强制降级为自身角色（防越权）"""
        resp = await client.get(
            "/api/v1/memory/dept", params={"dept_role": "warehouse"},
            headers=_auth(seed_user["token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["dept_role"] == seed_user["user"]["role"]

    async def test_admin_specify_dept(self, client, admin_user):
        resp = await client.get(
            "/api/v1/memory/dept", params={"dept_role": "purchase"},
            headers=_auth(admin_user["token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["dept_role"] == "purchase"

    async def test_dept_requires_login(self, client):
        resp = await client.get("/api/v1/memory/dept")
        assert resp.status_code == 401


# ============================================================
# 企业层：术语表（admin 权限）
# ============================================================

class TestGlossaryAPI:
    async def test_admin_add_and_public_read(self, client, admin_user, seed_user):
        resp = await client.post(
            "/api/v1/memory/glossary",
            json={"term": "SKU", "definition": "库存量单位"},
            headers=_auth(admin_user["token"]),
        )
        assert resp.status_code == 200

        # 普通用户可读（读取公开）
        resp = await client.get("/api/v1/memory/glossary", headers=_auth(seed_user["token"]))
        assert resp.status_code == 200
        assert resp.json()["terms"]["SKU"] == "库存量单位"

    async def test_non_admin_write_forbidden(self, client, seed_user):
        resp = await client.post(
            "/api/v1/memory/glossary",
            json={"term": "SKU", "definition": "库存量单位"},
            headers=_auth(seed_user["token"]),
        )
        assert resp.status_code == 403

    async def test_non_admin_delete_forbidden(self, client, admin_user, seed_user):
        await client.post(
            "/api/v1/memory/glossary",
            json={"term": "SKU", "definition": "库存量单位"},
            headers=_auth(admin_user["token"]),
        )
        resp = await client.delete(
            "/api/v1/memory/glossary/SKU", headers=_auth(seed_user["token"])
        )
        assert resp.status_code == 403

    async def test_admin_delete(self, client, admin_user):
        await client.post(
            "/api/v1/memory/glossary",
            json={"term": "SKU", "definition": "库存量单位"},
            headers=_auth(admin_user["token"]),
        )
        resp = await client.delete(
            "/api/v1/memory/glossary/SKU", headers=_auth(admin_user["token"])
        )
        assert resp.status_code == 200
        resp = await client.get("/api/v1/memory/glossary", headers=_auth(admin_user["token"]))
        assert resp.json()["count"] == 0

    async def test_glossary_requires_login(self, client):
        resp = await client.get("/api/v1/memory/glossary")
        assert resp.status_code == 401
