"""Feedback API 单元测试 — 之前 0 覆盖"""
import pytest

from helpers import patch_auth, post_feedback


@pytest.mark.asyncio
async def test_create_feedback_no_auth(client):
    """未认证 POST /feedback 应被 401/403 拒绝"""
    resp = await client.post(
        "/api/v1/feedback",
        json={
            "session_id": "sess-001",
            "query": "测试问题",
            "answer": "测试答案",
            "rating": 1,
        },
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_feedback_invalid_rating(client, seed_user, monkeypatch):
    """rating=5 超 Pydantic 约束（le=1）→ 422"""
    resp = await post_feedback(
        client, monkeypatch,
        {"session_id": "sess-001", "query": "Q", "answer": "A", "rating": 5},
        token=seed_user["token"],
    )
    assert resp.status_code == 422
    # Pydantic 返回 detail 是 list of errors
    assert any("rating" in str(err.get("loc", [])) for err in resp.json()["detail"])


@pytest.mark.asyncio
async def test_create_feedback_rating_zero_invalid(client, seed_user, monkeypatch):
    """rating=0 违反约束（ge=-1）→ 422"""
    resp = await post_feedback(
        client, monkeypatch,
        {"session_id": "sess-001", "query": "Q", "answer": "A", "rating": 0},
        token=seed_user["token"],
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_feedback_negative_rating_accepted(client, seed_user, monkeypatch):
    """rating=-1 应被接受（写库，不抛错）"""
    resp = await post_feedback(
        client, monkeypatch,
        {
            "session_id": "sess-001", "query": "Q", "answer": "A",
            "rating": -1, "comment": "答案不准",
        },
        token=seed_user["token"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rating"] == -1
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_feedback_missing_required_fields(client, seed_user, monkeypatch):
    """缺 session_id/query/answer → Pydantic 422"""
    resp = await post_feedback(
        client, monkeypatch, {"rating": 1},  # 缺 session_id, query, answer
        token=seed_user["token"],
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_feedback_with_optional_fields(client, seed_user, monkeypatch):
    """可选字段 sources/comment/confidence/intent 都能传"""
    resp = await post_feedback(
        client, monkeypatch,
        {
            "session_id": "sess-002", "query": "Q", "answer": "A", "rating": 1,
            "comment": "很好",
            "sources": [{"id": "src-1", "content": "ctx"}, {"id": "src-2", "content": "ctx2"}],
            "confidence": 0.95, "intent": "rag",
        },
        token=seed_user["token"],
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == 1


@pytest.mark.asyncio
async def test_feedback_stats_invalid_days_range(client, seed_user, monkeypatch):
    """days 参数超范围（0 或 >365）→ 422（Query 校验）"""
    patch_auth(monkeypatch)

    # days=0 违反 ge=1
    resp = await client.get(
        "/api/v1/feedback/stats?days=0",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 422

    # days=400 违反 le=365
    resp = await client.get(
        "/api/v1/feedback/stats?days=400",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_feedback_stats_empty_db_returns_zero(client, seed_user, monkeypatch):
    """空数据库时 satisfaction_rate 应为 0.0（避免除零）"""
    patch_auth(monkeypatch)

    resp = await client.get(
        "/api/v1/feedback/stats?days=30",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["satisfaction_rate"] == 0.0
    # 空库时 total_feedback 应为 0
    assert data["total_feedback"] == 0
