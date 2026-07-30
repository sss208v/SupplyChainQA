"""Tests for the evaluate API endpoints.

Covers:
  - GET  /evaluate/summary  (summary structure)
  - GET  /evaluate/full     (empty ground truth)
  - POST /evaluate/offline  (metrics format)
  - Unauthenticated access rejection
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# 1. Summary endpoint returns valid structure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.api.evaluate.rag_evaluator")
async def test_get_evaluation_summary(mock_evaluator, client, seed_user):
    """GET /evaluate/summary should return success=True with a summary dict."""
    mock_evaluator.get_summary.return_value = {
        "avg_recall_at_5": 0.78,
        "avg_ndcg_at_5": 0.82,
        "avg_mrr": 0.65,
        "avg_map": 0.71,
        "avg_retrieval_score": 0.75,
        "total_evaluations": 42,
    }

    resp = await client.get(
        "/api/v1/evaluate/summary",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    summary = data["summary"]
    assert "avg_recall_at_5" in summary
    assert "avg_ndcg_at_5" in summary
    assert "avg_mrr" in summary
    assert "total_evaluations" in summary
    # Values should be numeric
    assert isinstance(summary["avg_recall_at_5"], float)


# ---------------------------------------------------------------------------
# 2. Empty ground truth handled gracefully
# ---------------------------------------------------------------------------

# test_run_evaluation_empty_dataset 已移除：/full 不再依赖 ground_truth/proxy；
# “无官方结果”分支由 test_full_no_official_result 覆盖。


# ---------------------------------------------------------------------------
# 3. Metrics returned in expected format (offline evaluation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.api.evaluate.rag_evaluator")
async def test_evaluation_metrics_format(mock_evaluator, client, seed_user):
    """POST /evaluate/offline should return evaluation.to_dict() under 'evaluation'."""
    mock_result = MagicMock()
    mock_result.to_dict.return_value = {
        "recall_at_5": 0.8,
        "precision_at_3": 0.6,
        "mrr": 0.75,
        "ndcg_at_5": 0.85,
        "map_score": 0.7,
        "retrieval_score": 0.78,
    }
    mock_evaluator.evaluate_retrieval.return_value = mock_result

    resp = await client.post(
        "/api/v1/evaluate/offline",
        json={
            "query": "MAT-001 库存",
            "retrieved_chunk_ids": ["c1", "c2", "c3"],
            "relevant_chunk_ids": ["c1", "c3"],
        },
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    evaluation = data["evaluation"]
    assert "recall_at_5" in evaluation
    assert "mrr" in evaluation
    assert "ndcg_at_5" in evaluation
    # Verify the mock was called with the right arguments
    mock_evaluator.evaluate_retrieval.assert_called_once_with(
        query="MAT-001 库存",
        retrieved_chunk_ids=["c1", "c2", "c3"],
        relevant_chunk_ids=["c1", "c3"],
    )


# ---------------------------------------------------------------------------
# 4. Unauthenticated access rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluation_requires_auth(client):
    """All evaluate endpoints require a valid auth token."""
    # Summary endpoint without token
    resp = await client.get("/api/v1/evaluate/summary")
    assert resp.status_code in (401, 403)

    # Offline endpoint without token
    resp = await client.post(
        "/api/v1/evaluate/offline",
        json={
            "query": "test",
            "retrieved_chunk_ids": ["c1"],
            "relevant_chunk_ids": ["c1"],
        },
    )
    assert resp.status_code in (401, 403)

    # Online endpoint without token
    resp = await client.post(
        "/api/v1/evaluate/online",
        json={"query": "test", "top_k": 5},
    )
    assert resp.status_code in (401, 403)

    # Full evaluation endpoint without token
    resp = await client.get("/api/v1/evaluate/full")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 5. Online evaluation returns quality label
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.api.evaluate.rag_evaluator")
async def test_online_evaluation_returns_quality_label(mock_evaluator, client, seed_user):
    """POST /evaluate/online returns quality_label among other fields."""
    mock_evaluator.evaluate_online.return_value = {
        "avg_rerank_score": 0.92,
        "vector_ratio": 0.7,
        "bm25_ratio": 0.3,
        "quality_label": "excellent",
        "top_scores": [0.95, 0.91, 0.88],
    }

    resp = await client.post(
        "/api/v1/evaluate/online",
        json={"query": "供应商准入资质", "top_k": 5},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    evaluation = data["evaluation"]
    assert "quality_label" in evaluation
    assert evaluation["quality_label"] == "excellent"
    assert "avg_rerank_score" in evaluation


# ---------------------------------------------------------------------------
# 补测：/judge 端点（之前 0 覆盖）+ /summary exception + /full 成功路径
# + Pydantic 校验（top_k 范围、retrieved_contexts 长度上限）
# ---------------------------------------------------------------------------


# ---- /judge 端点 ----


@pytest.mark.asyncio
@patch("app.api.evaluate.LLMFactory")
async def test_judge_success(mock_factory, client, seed_user):
    """LLM 正常返回 JSON 格式的评判结果"""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content='{"answer_correctness": 5, "answer_relevance": 4, "context_utilization": 4, "hallucination": 5, "overall_score": 4.5, "feedback": "good"}')
    )
    mock_factory.get_llm.return_value = fake_llm

    resp = await client.post(
        "/api/v1/evaluate/judge",
        json={
            "query": "MAT-001 库存多少？",
            "retrieved_contexts": ["MAT-001 当前库存 100 件", "近 30 天入库 50"],
            "generated_answer": "MAT-001 当前库存 100 件",
            "reference_answer": "100",
        },
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["judge_result"]["answer_correctness"] == 5
    assert data["judge_result"]["overall_score"] == 4.5


@pytest.mark.asyncio
@patch("app.api.evaluate.LLMFactory")
async def test_judge_invalid_json_returns_raw(mock_factory, client, seed_user):
    """LLM 返回非 JSON 时，parse_llm_json 抛错 → fallback 到 raw_output"""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=MagicMock(content="我无法评估这个回答"))
    mock_factory.get_llm.return_value = fake_llm

    resp = await client.post(
        "/api/v1/evaluate/judge",
        json={
            "query": "测试问题",
            "retrieved_contexts": ["上下文1"],
            "generated_answer": "答案1",
        },
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "raw_output" in data["judge_result"]
    assert data["judge_result"]["raw_output"] == "我无法评估这个回答"


@pytest.mark.asyncio
@patch("app.api.evaluate.LLMFactory")
async def test_judge_llm_failure_returns_500(mock_factory, client, seed_user):
    """LLM 调用抛错 → 500 错误"""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(
        side_effect=ConnectionError("LLM 服务不可用")
    )
    mock_factory.get_llm.return_value = fake_llm

    resp = await client.post(
        "/api/v1/evaluate/judge",
        json={
            "query": "测试",
            "retrieved_contexts": ["ctx"],
            "generated_answer": "ans",
        },
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 500
    assert "评判失败" in resp.json()["detail"]


# ---- /summary exception 路径 ----


@pytest.mark.asyncio
@patch("app.api.evaluate.rag_evaluator")
async def test_summary_exception_returns_500(mock_evaluator, client, seed_user):
    """rag_evaluator.get_summary() 抛错 → 500"""
    mock_evaluator.get_summary.side_effect = RuntimeError("db connection lost")

    resp = await client.get(
        "/api/v1/evaluate/summary",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 500
    assert "获取汇总失败" in resp.json()["detail"]


# ---- /full 成功路径 ----


@pytest.mark.asyncio
async def test_full_returns_official_ragas(client, seed_user, monkeypatch, tmp_path):
    """/full 返回最近一次官方 RAGAS 落盘结果（含 ragas_metrics 四项），不再计算 proxy"""
    import json as _json
    import glob as _glob
    result_file = tmp_path / "result_official.json"
    result_file.write_text(_json.dumps({
        "judge_model": "deepseek-v4-flash",
        "gen_model": "Qwen3-14B",
        "valid_samples": 20,
        "overall": 0.79,
        "ragas_metrics": {
            "faithfulness": 0.803, "answer_relevancy": 0.839,
            "context_precision": 0.693, "context_recall": 0.825,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(_glob, "glob", lambda pattern: [str(result_file)])

    resp = await client.get(
        "/api/v1/evaluate/full",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["official"] is True
    assert data["judge_model"] == "deepseek-v4-flash"
    m = data["metrics"]
    assert set(m.keys()) == {"faithfulness", "answer_relevancy", "context_precision", "context_recall"}
    assert m["faithfulness"] == 0.803


@pytest.mark.asyncio
async def test_full_no_official_result(client, seed_user, monkeypatch):
    """无官方 RAGAS 结果时返回 success=False + 引导信息（不回退 proxy）"""
    import glob as _glob
    monkeypatch.setattr(_glob, "glob", lambda pattern: [])
    resp = await client.get(
        "/api/v1/evaluate/full",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "RAGAS" in data["error"]


# ---- Pydantic 校验 ----


@pytest.mark.asyncio
async def test_online_topk_out_of_range(client, seed_user):
    """top_k 超过上限（1-20）→ 422"""
    resp = await client.post(
        "/api/v1/evaluate/online",
        json={"query": "测试", "top_k": 100},  # 超过 20
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_judge_contexts_exceed_limit_rejected(client, seed_user):
    """retrieved_contexts > 20 条 → 422（Pydantic max_length 防 LLM DoS）"""
    resp = await client.post(
        "/api/v1/evaluate/judge",
        json={
            "query": "测试",
            "retrieved_contexts": [f"ctx-{i}" for i in range(21)],  # 超过 max_length=20
            "generated_answer": "ans",
        },
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_judge_exactly_20_contexts_accepted(client, seed_user):
    """retrieved_contexts 正好 20 条 → 200（边界值）"""
    with patch("app.api.evaluate.LLMFactory") as mock_factory:
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content='{"answer_correctness": 3}')
        )
        mock_factory.get_llm.return_value = fake_llm

        resp = await client.post(
            "/api/v1/evaluate/judge",
            json={
                "query": "测试",
                "retrieved_contexts": [f"ctx-{i}" for i in range(20)],  # 正好 20
                "generated_answer": "ans",
            },
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_judge_with_reference_answer(client, seed_user):
    """reference_answer 可选 —— 不传也能通过校验"""
    # 同样 patch LLM 让它不真的调用
    with patch("app.api.evaluate.LLMFactory") as mock_factory:
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content='{"answer_correctness": 3}')
        )
        mock_factory.get_llm.return_value = fake_llm

        resp = await client.post(
            "/api/v1/evaluate/judge",
            json={
                "query": "测试",
                "retrieved_contexts": ["ctx"],
                "generated_answer": "ans",
                # 不传 reference_answer
            },
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200
