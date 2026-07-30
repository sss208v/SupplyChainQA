"""知识库上传 RBAC 越权防护测试（P0-1）

验证 security_group 由服务端根据当前用户角色权威裁决：
- 非 admin 用户无法将文档挂到其他部门/admin 名下
- admin 传入非法角色返回 400
"""
import io
import pytest


def _mock_index(monkeypatch, captured: dict):
    """mock 解析与入库，捕获最终写入的 security_group"""
    from app.api import knowledge as kmod

    def fake_index(did, chunks, security_group):
        captured["sg"] = security_group
        return {"chunk_count": 0}

    monkeypatch.setattr(kmod, "_parse_and_chunk", lambda fp, fn, did: [])
    monkeypatch.setattr(kmod.rag_engine, "index_document", fake_index)


@pytest.mark.asyncio
async def test_upload_escalation_to_admin_downgraded(client, seed_user, monkeypatch):
    """purchase 用户请求 security_group=admin → 强制降级为 purchase"""
    captured = {}
    _mock_index(monkeypatch, captured)

    resp = await client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        data={"security_group": "admin"},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    assert captured["sg"] == ["purchase"]
    assert resp.json()["security_group"] == ["purchase"]


@pytest.mark.asyncio
async def test_upload_escalation_to_other_dept_downgraded(client, seed_user, monkeypatch):
    """purchase 用户请求 finance,logistics → 全部丢弃，只保留自身角色"""
    captured = {}
    _mock_index(monkeypatch, captured)

    resp = await client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        data={"security_group": "finance,logistics,admin"},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    assert captured["sg"] == ["purchase"]


@pytest.mark.asyncio
async def test_upload_public_requires_config(client, seed_user, monkeypatch):
    """非 admin 附加 public：默认 ALLOW_PUBLIC_UPLOAD=False → 被丢弃"""
    captured = {}
    _mock_index(monkeypatch, captured)

    resp = await client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        data={"security_group": "purchase,public"},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    assert captured["sg"] == ["purchase"]


@pytest.mark.asyncio
async def test_upload_public_allowed_when_enabled(client, seed_user, monkeypatch):
    """ALLOW_PUBLIC_UPLOAD=True 时非 admin 可附加 public"""
    captured = {}
    _mock_index(monkeypatch, captured)

    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "ALLOW_PUBLIC_UPLOAD", True)

    resp = await client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        data={"security_group": "public"},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    assert captured["sg"] == ["purchase", "public"]


@pytest.mark.asyncio
async def test_upload_admin_invalid_role_rejected(client, admin_user, monkeypatch):
    """admin 传入白名单外的角色 → 400"""
    captured = {}
    _mock_index(monkeypatch, captured)

    resp = await client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        data={"security_group": "admin,hacker_group"},
        headers={"Authorization": f"Bearer {admin_user['token']}"},
    )
    assert resp.status_code == 400
    assert "非法权限角色" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_admin_empty_defaults_to_admin(client, admin_user, monkeypatch):
    """admin 不传 security_group → 默认 ['admin']"""
    captured = {}
    _mock_index(monkeypatch, captured)

    resp = await client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        headers={"Authorization": f"Bearer {admin_user['token']}"},
    )
    assert resp.status_code == 200
    assert captured["sg"] == ["admin"]


def test_resolve_security_groups_pure():
    """纯函数级校验：不经过 HTTP 也能保证裁决逻辑正确"""
    from app.api.knowledge import _resolve_security_groups

    # 非 admin 越权 → 降级
    assert _resolve_security_groups("admin,finance", "warehouse") == ["warehouse"]
    # admin 合法自由指定
    assert _resolve_security_groups("finance,public", "admin") == ["finance", "public"]
    # admin 空输入 → 默认 admin
    assert _resolve_security_groups("", "admin") == ["admin"]
