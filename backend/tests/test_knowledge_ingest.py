"""知识库 /ingest 端点鉴权与 index_document 幂等性测试

- POST /ingest 与 GET /ingest/status 仅 admin 可访问（匿名 401、非 admin 403、admin 200）
- index_document 重复入库同一 doc_id 时先删旧数据，避免 Milvus 累积重复 chunk
"""
import pytest


@pytest.mark.asyncio
async def test_ingest_anonymous_rejected(client):
    """匿名调用 /ingest → 401（未携带 token）"""
    resp = await client.post("/api/v1/knowledge/ingest")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingest_non_admin_forbidden(client, seed_user):
    """非 admin 角色调用 /ingest → 403"""
    resp = await client.post(
        "/api/v1/knowledge/ingest",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_admin_allowed(client, admin_user, monkeypatch):
    """admin 调用 /ingest → 200，后台任务被提交（mock 掉实际执行）"""
    from app.api import knowledge as kmod

    monkeypatch.setattr(kmod, "_run_ingest_job", lambda: None)

    resp = await client.post(
        "/api/v1/knowledge/ingest",
        headers={"Authorization": f"Bearer {admin_user['token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_ingest_status_non_admin_forbidden(client, seed_user):
    """非 admin 查询入库状态 → 403（与 POST 同一 UI 流程收紧）"""
    resp = await client.get(
        "/api/v1/knowledge/ingest/status",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 403


def test_index_document_deletes_before_insert(monkeypatch):
    """同一 doc_id 重复入库：delete_by_doc_id 必须在 batch_insert 之前调用（幂等 upsert）"""
    from app.core.rag import engine as engine_mod

    calls = []

    monkeypatch.setattr(
        engine_mod.milvus_manager, "delete_by_doc_id",
        lambda doc_id: calls.append(("delete", doc_id)),
    )
    monkeypatch.setattr(
        engine_mod.milvus_manager, "batch_insert",
        lambda records: calls.append(("insert", len(records))) or {"insert_count": len(records)},
    )
    monkeypatch.setattr(
        engine_mod.rag_engine.embedding, "embed_documents",
        lambda texts: [[0.0] * 4 for _ in texts],
    )
    monkeypatch.setattr(
        engine_mod.rag_engine.bm25, "index_documents",
        lambda doc_id, chunks, security_group=None: None,
    )

    chunks = [{"chunk_id": "c1", "content": "hello"}]
    engine_mod.rag_engine.index_document("doc-1", chunks, security_group=["admin"])

    # delete 必须先于 insert，且顺序正确
    assert calls[0] == ("delete", "doc-1")
    assert calls[1][0] == "insert"
