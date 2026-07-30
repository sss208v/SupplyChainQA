"""Chat API 集成测试"""
import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "SupplyChainRAG"
    assert "version" in data
    assert "services" in data


@pytest.mark.asyncio
async def test_config(client):
    resp = await client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_provider" in data
    assert "embedding_model" in data
    assert "chunk_size" in data


@pytest.mark.asyncio
async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "app" in data
    assert "docs" in data


@pytest.mark.asyncio
async def test_model_list(client):
    resp = await client.get("/api/v1/chat/model/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "current" in data
    assert "models" in data
    # 内置 provider 都应返回（含本地 OpenAI 兼容端点 local）
    providers = {m["provider"] for m in data["models"]}
    assert providers == {"local", "deepseek", "minimax", "ollama"}


@pytest.mark.asyncio
async def test_chat_stream_returns_sse(client, seed_user):
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"query": "你好", "session_id": "test-sse-001"},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "text/event-stream" in content_type or "application/json" in content_type


@pytest.mark.asyncio
async def test_chat_stream_without_auth(client):
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"query": "你好", "session_id": "test-no-auth"},
    )
    # 未认证可能返回 401 或仍然允许（取决于端点配置）
    assert resp.status_code in (200, 401, 403)


# ---------------------------------------------------------------------------
# 补测：/model/switch, /ask, /sql — 这三个 endpoint 之前 0 覆盖
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_model_deepseek(client):
    resp = await client.post(
        "/api/v1/chat/model/switch",
        json={"provider": "deepseek"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "deepseek"
    assert "已切换" in data["message"]


@pytest.mark.asyncio
async def test_switch_model_invalid_provider(client):
    resp = await client.post(
        "/api/v1/chat/model/switch",
        json={"provider": "gpt-4-turbo"},
    )
    assert resp.status_code == 400
    assert "不支持的provider" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_switch_model_missing_provider(client):
    """空 body / 缺 provider — 默认空串，触发 400 分支"""
    resp = await client.post("/api/v1/chat/model/switch", json={})
    assert resp.status_code == 400


# --- /ask 端点：直接 RAG pipeline，区分成功 / LLM 不可用 / 其他错误 ---


@pytest.mark.asyncio
async def test_ask_success(monkeypatch, client):
    from app.api.handlers import ask as ask_module

    async def fake_answer(query, session_id=None, doc_ids=None):
        return {
            "answer": "这是 mock 出来的答案",
            "sources": [{"id": "src-1", "content": "原文片段"}],
            "confidence": 0.87,
            "query_type": "rag",
            "context_used": 3,
        }

    monkeypatch.setattr(ask_module.rag_agent, "answer", fake_answer)

    resp = await client.post("/api/v1/chat/ask", json={"question": "MAT-001 库存多少？"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "这是 mock 出来的答案"
    assert data["confidence"] == 0.87
    assert data["context_used"] == 3
    assert "error" not in data


@pytest.mark.asyncio
async def test_ask_llm_unavailable(monkeypatch, client):
    """ConnectionError 应被识别为 LLM 不可用，返回特定错误信息"""
    from app.api.handlers import ask as ask_module

    async def fake_answer(query, session_id=None, doc_ids=None):
        raise ConnectionError("Connection refused: localhost:8080")

    monkeypatch.setattr(ask_module.rag_agent, "answer", fake_answer)

    resp = await client.post("/api/v1/chat/ask", json={"question": "测试"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == ""
    assert data["confidence"] == 0.0
    assert "llama.cpp" in data["error"] or "LLM" in data["error"]


@pytest.mark.asyncio
async def test_ask_generic_error(monkeypatch, client):
    """非 LLM 错误（除以零、KeyError 等）走通用错误分支"""
    from app.api.handlers import ask as ask_module

    async def fake_answer(query, session_id=None, doc_ids=None):
        raise ValueError("vector dimension mismatch")

    monkeypatch.setattr(ask_module.rag_agent, "answer", fake_answer)

    resp = await client.post("/api/v1/chat/ask", json={"question": "测试"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"].startswith("处理请求时出错")
    assert "ValueError" in data["error"]


# --- /sql 端点：mock text_to_sql 避免真实数据库 ---


@pytest.mark.asyncio
async def test_sql_query_success(monkeypatch, client):
    from types import SimpleNamespace
    from app.core import text_to_sql as t2s_module

    class FakeEngine:
        async def execute(self, question, user_role):
            return SimpleNamespace(
                sql="SELECT id, name FROM materials LIMIT 100",
                columns=["id", "name"],
                rows=[["MAT-001", "钢材"], ["MAT-002", "铜材"]],
                row_count=2,
                elapsed_ms=15.3,
                error=None,
                execution_ms=12.0,
            )

        @staticmethod
        def format_result(result):
            return "| id     | name |\n|--------|------|\n| MAT-001 | 钢材 |"

    # chat.py 在 sql_query 函数内 import get_text_to_sql，所以 patch 真实模块路径
    monkeypatch.setattr(t2s_module, "get_text_to_sql", lambda: FakeEngine())

    resp = await client.post(
        "/api/v1/chat/sql",
        json={"question": "查询所有物料", "user_role": "admin"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sql"].startswith("SELECT")
    assert data["row_count"] == 2
    assert data["columns"] == ["id", "name"]


@pytest.mark.asyncio
async def test_sql_query_validation_empty_question(client):
    """Pydantic min_length=1 校验：空字符串应被 422 拒绝"""
    resp = await client.post("/api/v1/chat/sql", json={"question": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sql_query_uses_auth_role(monkeypatch, client, seed_user):
    """sql_query 行为：尝试用 token 中的 role，失败则 fallback 到 body role"""
    from types import SimpleNamespace
    from app.core import text_to_sql as t2s_module

    captured = {}

    class FakeEngine:
        async def execute(self, question, user_role):
            captured["user_role"] = user_role
            return SimpleNamespace(
                sql="SELECT 1",
                columns=["?column?"],
                rows=[[1]],
                row_count=1,
                elapsed_ms=1.0,
                error=None,
                execution_ms=0.5,
            )

        @staticmethod
        def format_result(result):
            return "?column?\n----------\n1"

    monkeypatch.setattr(t2s_module, "get_text_to_sql", lambda: FakeEngine())

    # 路径 1：无 Authorization → 强制 employee（防 body 提权）
    resp_anon = await client.post(
        "/api/v1/chat/sql",
        json={"question": "查询", "user_role": "admin"},  # 试图提权
    )
    assert resp_anon.status_code == 200
    # 即使 body 传 admin，无 token 也会被强制为 employee
    assert captured["user_role"] == "employee"

    # 路径 2：无效 Authorization → 拒绝（401，不再 body fallback）
    resp_bad_token = await client.post(
        "/api/v1/chat/sql",
        json={"question": "查询", "user_role": "employee"},
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert resp_bad_token.status_code == 401

    # 路径 3：body 注入非白名单角色 → 403
    resp_evil = await client.post(
        "/api/v1/chat/sql",
        json={"question": "查询", "user_role": "hacker_role"},
    )
    assert resp_evil.status_code == 403
