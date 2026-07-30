"""认证 API 集成测试"""
import pytest


@pytest.mark.asyncio
async def test_register_success(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "newuser",
        "password": "pass123456",
        "department": "采购部",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["user"]["username"] == "newuser"
    assert data["user"]["role"] == "purchase"


@pytest.mark.asyncio
async def test_register_duplicate_username(client, seed_user):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "testuser",
        "password": "pass123456",
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_short_password(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "shortpwd",
        "password": "123",
    })
    assert resp.status_code == 422  # Pydantic validation


@pytest.mark.asyncio
async def test_login_success(client, seed_user):
    resp = await client.post("/api/v1/auth/login", json={
        "username": "testuser",
        "password": "test123456",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["user"]["username"] == "testuser"


@pytest.mark.asyncio
async def test_login_wrong_password(client, seed_user):
    resp = await client.post("/api/v1/auth/login", json={
        "username": "testuser",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    resp = await client.post("/api/v1/auth/login", json={
        "username": "nobody",
        "password": "whatever",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(client, seed_user):
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "testuser"


@pytest.mark.asyncio
async def test_me_without_token(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout(client, seed_user):
    resp = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    # 登出后 token 应失效
    resp2 = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp2.status_code == 401
