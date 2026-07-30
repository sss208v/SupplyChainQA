"""对话接口认证边界测试（P1-1）

REQUIRE_AUTH_CHAT=True 时 /chat/stream 与 /chat/ask 必须拒绝匿名请求；
测试环境默认 false（conftest 设置），本文件通过 monkeypatch 单独开启。
"""
import pytest


@pytest.fixture
def require_auth(monkeypatch):
    """开启对话接口强制认证"""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "REQUIRE_AUTH_CHAT", True)


@pytest.mark.asyncio
async def test_chat_stream_anonymous_rejected(client, require_auth):
    """强制认证下，匿名调用 /chat/stream → 401"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"query": "库存怎么查"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_ask_anonymous_rejected(client, require_auth):
    """强制认证下，匿名调用 /chat/ask → 401"""
    resp = await client.post(
        "/api/v1/chat/ask",
        json={"question": "库存怎么查"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cache_stats_requires_admin(client, seed_user):
    """/chat/cache/stats 仅 admin 可访问：purchase 用户 → 403"""
    resp = await client.get(
        "/api/v1/chat/cache/stats",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cache_stats_admin_ok(client, admin_user):
    """admin 访问 /chat/cache/stats → 返回各层命中率结构"""
    resp = await client.get(
        "/api/v1/chat/cache/stats",
        headers={"Authorization": f"Bearer {admin_user['token']}"},
    )
    assert resp.status_code == 200
    layers = resp.json()["layers"]
    for layer in ("l1", "l2", "l3"):
        assert "hits" in layers[layer]
        assert "misses" in layers[layer]
        assert "hit_rate" in layers[layer]


@pytest.mark.asyncio
async def test_cache_stats_anonymous_rejected(client):
    """匿名访问 /chat/cache/stats → 401"""
    resp = await client.get("/api/v1/chat/cache/stats")
    assert resp.status_code == 401
