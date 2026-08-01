"""部门内分级权限测试（部门 × 级别 二维 RBAC）

覆盖：
- check_level / require_level 级别矩阵（admin/manager/employee）
- 知识库：employee 上传 403；manager 上传 200；manager 删除跨部门文档 403
- 工具：employee 调写工具 403、只读工具 200；manager 写工具 200
- 部门记忆：employee 写 403、读 200
- 注册默认 employee；seed 用户 manager；/me 返回 level
"""
import pytest
from helpers import upload_knowledge


def _mock_index(monkeypatch, captured: dict):
    """mock 解析与入库（与 test_knowledge_rbac 一致）"""
    from app.api import knowledge as kmod

    def fake_index(did, chunks, security_group):
        captured["sg"] = security_group
        return {"chunk_count": 0}

    monkeypatch.setattr(kmod, "_parse_and_chunk", lambda fp, fn, did: [])
    monkeypatch.setattr(kmod.rag_engine, "index_document", fake_index)


# ============================================================
# check_level / require_level 级别矩阵
# ============================================================

class TestCheckLevel:
    def test_admin_passes_all(self):
        from app.core.auth import check_level

        check_level({"role": "admin", "level": "admin"}, "admin")
        check_level({"role": "admin", "level": "admin"}, "manager")
        check_level({"role": "admin", "level": "admin"}, "employee")

    def test_admin_role_without_level_compat(self):
        """旧数据：role=admin 无 level 字段也应放行"""
        from app.core.auth import check_level

        check_level({"role": "admin", "level": "employee"}, "manager")

    def test_manager_passes_manager_and_employee(self):
        from app.core.auth import check_level

        check_level({"role": "purchase", "level": "manager"}, "manager")
        check_level({"role": "purchase", "level": "manager"}, "employee")

    def test_manager_fails_admin(self):
        from fastapi import HTTPException

        from app.core.auth import check_level

        with pytest.raises(HTTPException) as exc:
            check_level({"role": "purchase", "level": "manager"}, "admin")
        assert exc.value.status_code == 403

    def test_employee_fails_manager(self):
        from fastapi import HTTPException

        from app.core.auth import check_level

        with pytest.raises(HTTPException) as exc:
            check_level({"role": "purchase", "level": "employee"}, "manager")
        assert exc.value.status_code == 403

    def test_unknown_level_treated_as_employee(self):
        from fastapi import HTTPException

        from app.core.auth import check_level

        with pytest.raises(HTTPException):
            check_level({"role": "purchase", "level": "hacker"}, "manager")


class TestRequireLevel:
    class _Req:
        pass

    async def test_require_level_allows_manager(self, monkeypatch):
        """装饰器对 manager 放行、对 employee 拒绝"""
        import app.core.auth as auth_mod

        async def fake_full(request):
            return {"role": "purchase", "level": "manager", "user_id": 1}

        monkeypatch.setattr(auth_mod, "get_current_user_full", fake_full)
        checker = auth_mod.require_level("manager", "admin")

        result = await checker(self._Req())
        assert result["level"] == "manager"

    async def test_require_level_admin_role_bypass(self, monkeypatch):
        """role=admin 无需 level 字段也通过装饰器"""
        import app.core.auth as auth_mod

        async def fake_full(request):
            return {"role": "admin", "level": "employee", "user_id": 1}

        monkeypatch.setattr(auth_mod, "get_current_user_full", fake_full)
        checker = auth_mod.require_level("manager")

        result = await checker(self._Req())
        assert result["role"] == "admin"


# ============================================================
# 知识库：上传/删除按级别
# ============================================================

class TestKnowledgeLevelRBAC:
    async def test_employee_upload_forbidden(self, client, employee_user, monkeypatch):
        """employee 上传 → 403"""
        captured = {}
        _mock_index(monkeypatch, captured)

        resp = await upload_knowledge(client, employee_user["token"])
        assert resp.status_code == 403
        assert "级别" in resp.json()["detail"]

    async def test_manager_upload_allowed(self, client, seed_user, monkeypatch):
        """manager（seed_user）上传 → 200，权限组仍强制自身部门"""
        captured = {}
        _mock_index(monkeypatch, captured)

        resp = await upload_knowledge(client, seed_user["token"])
        assert resp.status_code == 200
        assert captured["sg"] == ["purchase"]

    async def test_employee_delete_forbidden(self, client, employee_user):
        resp = await client.delete(
            "/api/v1/knowledge/doc-1",
            headers={"Authorization": f"Bearer {employee_user['token']}"},
        )
        assert resp.status_code == 403

    async def test_manager_delete_own_dept_allowed(self, client, seed_user, monkeypatch):
        """manager 删除本部门文档：Milvus 不可用时 list 为空 → 403；
        这里 mock list_documents 返回本部门文档验证放行路径"""
        from unittest.mock import AsyncMock

        from app.api import knowledge as kmod

        monkeypatch.setattr(
            kmod.milvus_manager, "list_documents",
            lambda role: [{"doc_id": "doc-1", "security_group": ["purchase"]}],
        )
        monkeypatch.setattr(
            kmod.milvus_manager, "delete_by_doc_id",
            lambda doc_id: None,
        )
        monkeypatch.setattr(kmod.rag_engine.bm25, "remove_doc", lambda doc_id: None)
        monkeypatch.setattr(kmod, "_invalidate_retrieval_caches", AsyncMock())

        resp = await client.delete(
            "/api/v1/knowledge/doc-1",
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200

    async def test_manager_delete_cross_dept_forbidden(self, client, seed_user, monkeypatch):
        """manager 删除非本部门文档 → 403（list_documents 按角色过滤后不含目标）"""
        from app.api import knowledge as kmod

        monkeypatch.setattr(
            kmod.milvus_manager, "list_documents",
            lambda role: [{"doc_id": "other-doc", "security_group": ["finance"]}],
        )

        resp = await client.delete(
            "/api/v1/knowledge/doc-1",
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 403

    async def test_admin_delete_any_doc_allowed(self, client, admin_user, monkeypatch):
        """admin 删除任意文档：跳过归属校验"""
        from unittest.mock import AsyncMock

        from app.api import knowledge as kmod

        monkeypatch.setattr(
            kmod.milvus_manager, "delete_by_doc_id",
            lambda doc_id: None,
        )
        monkeypatch.setattr(kmod.rag_engine.bm25, "remove_doc", lambda doc_id: None)
        monkeypatch.setattr(kmod, "_invalidate_retrieval_caches", AsyncMock())

        resp = await client.delete(
            "/api/v1/knowledge/doc-9",
            headers={"Authorization": f"Bearer {admin_user['token']}"},
        )
        assert resp.status_code == 200


# ============================================================
# 工具：写工具按级别
# ============================================================

class TestToolLevelRBAC:
    def test_write_tool_requires_manager_pure(self):
        """纯函数：employee 无写工具权限，manager/admin 有"""
        from app.api.tool import _is_tool_allowed

        # 只读工具：employee 可用
        assert _is_tool_allowed("get_knowledge", "purchase", "employee") is True
        # 写工具：employee 拒绝
        assert _is_tool_allowed("create_ticket", "purchase", "employee") is False
        # 写工具：manager/admin 放行
        assert _is_tool_allowed("create_ticket", "purchase", "manager") is True
        assert _is_tool_allowed("create_ticket", "purchase", "admin") is True

    async def test_employee_call_write_tool_forbidden(self, client, employee_user):
        resp = await client.post(
            "/api/v1/tools/call",
            json={"query": "创建工单", "tool_names": ["create_ticket"]},
            headers={"Authorization": f"Bearer {employee_user['token']}"},
        )
        assert resp.status_code == 403

    async def test_employee_call_read_tool_allowed(self, client, employee_user, monkeypatch):
        """employee 调只读工具：进入 Agent 执行（mock 避免真实 LLM）"""
        from unittest.mock import AsyncMock

        from app.api import tool as tmod

        fake_agent = AsyncMock()
        fake_agent.run = AsyncMock(return_value={
            "answer": "ok", "tool_calls": [], "iterations": 0,
        })
        monkeypatch.setattr(tmod, "tool_agent", fake_agent)

        resp = await client.post(
            "/api/v1/tools/call",
            json={"query": "查一下日期", "tool_names": ["get_datetime"]},
            headers={"Authorization": f"Bearer {employee_user['token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == "ok"

    async def test_manager_call_write_tool_allowed(self, client, seed_user, monkeypatch):
        from unittest.mock import AsyncMock

        from app.api import tool as tmod

        fake_agent = AsyncMock()
        fake_agent.run = AsyncMock(return_value={
            "answer": "工单已创建", "tool_calls": [], "iterations": 0,
        })
        monkeypatch.setattr(tmod, "tool_agent", fake_agent)

        resp = await client.post(
            "/api/v1/tools/call",
            json={"query": "创建工单", "tool_names": ["create_ticket"]},
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200

    async def test_employee_tool_list_excludes_write_tools(self, client, employee_user):
        """employee 的工具列表不包含写工具（信息最小化）"""
        resp = await client.get(
            "/api/v1/tools/list",
            headers={"Authorization": f"Bearer {employee_user['token']}"},
        )
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tools"]]
        assert "create_ticket" not in names


# ============================================================
# 部门记忆：employee 只读
# ============================================================

class TestDeptMemoryLevelRBAC:
    async def test_employee_write_forbidden(self, client, employee_user):
        resp = await client.post(
            "/api/v1/memory/dept/notes",
            json={"content": "越权写入"},
            headers={"Authorization": f"Bearer {employee_user['token']}"},
        )
        assert resp.status_code == 403

    async def test_employee_read_allowed(self, client, employee_user):
        resp = await client.get(
            "/api/v1/memory/dept",
            headers={"Authorization": f"Bearer {employee_user['token']}"},
        )
        assert resp.status_code == 200

    async def test_manager_write_allowed(self, client, seed_user):
        resp = await client.post(
            "/api/v1/memory/dept/notes",
            json={"content": "经理沉淀的部门记忆"},
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200


# ============================================================
# 注册默认级别与 /me
# ============================================================

class TestAuthLevel:
    async def test_register_defaults_employee(self, client, employee_user):
        assert employee_user["user"]["level"] == "employee"

    async def test_me_returns_level(self, client, seed_user):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["level"] == "manager"

    async def test_seed_user_is_manager(self, client, seed_user):
        assert seed_user["user"]["level"] == "manager"
