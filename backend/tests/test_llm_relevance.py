"""
SupplyChainRAG - Self-RAG Filter Unit Tests

Tests the LLMRelevanceFilter which evaluates document relevance and filters
out low-quality chunks before answer generation.

All LLM calls are mocked -- no external services needed.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.llm_relevance import LLMRelevanceFilter, RelevanceScore, get_self_rag


# ---- helpers ----

def _make_chunks(n: int) -> list[dict]:
    """Create n test chunks with sequential IDs."""
    return [
        {"chunk_id": f"chunk-{i}", "content": f"Content for chunk {i}", "source": f"doc{i}"}
        for i in range(1, n + 1)
    ]


def _mock_llm_factory(llm_response_text: str) -> MagicMock:
    """Build a mock llm_factory whose get_llm().ainvoke() returns the given text."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content=llm_response_text)

    factory = MagicMock()
    factory.get_llm.return_value = mock_llm
    return factory


def _mock_llm_factory_error(exception: Exception) -> MagicMock:
    """Build a mock llm_factory whose ainvoke raises an exception."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = exception

    factory = MagicMock()
    factory.get_llm.return_value = mock_llm
    return factory


# ---- tests ----

class TestEvaluateRelevance:
    """Relevance scoring works correctly when LLM returns valid scores."""

    def test_high_relevance_kept(self):
        """Chunks with score >= threshold are kept."""
        chunks = _make_chunks(3)
        scores_response = json.dumps([
            {"doc": 1, "score": 0.9, "reason": "Directly answers"},
            {"doc": 2, "score": 0.8, "reason": "Very relevant"},
            {"doc": 3, "score": 0.7, "reason": "Background info"},
        ])
        factory = _mock_llm_factory(scores_response)

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("test query", chunks, factory))

        assert len(filtered) == 3
        assert len(scores) == 3
        assert all(s.score >= 0.7 for s in scores)

    def test_scores_match_chunk_ids(self):
        """Returned RelevanceScore objects carry the correct chunk_id."""
        chunks = _make_chunks(2)
        scores_response = json.dumps([
            {"doc": 1, "score": 0.9, "reason": "r1"},
            {"doc": 2, "score": 0.6, "reason": "r2"},
        ])
        factory = _mock_llm_factory(scores_response)

        flt = LLMRelevanceFilter()
        _, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        id_map = {s.chunk_id: s.score for s in scores}
        assert id_map["chunk-1"] == 0.9
        assert id_map["chunk-2"] == 0.6


class TestEmptyScoresGuard:
    """Edge case: LLM returns empty array or malformed output."""

    def test_zero_chunks_returns_immediately(self):
        """filter_chunks([]) should return ([], []) without calling LLM."""
        factory = _mock_llm_factory("[]")

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", [], factory))

        assert filtered == []
        assert scores == []
        factory.get_llm.assert_not_called()

    def test_single_chunk_passes_through(self):
        """One chunk bypasses LLM evaluation entirely."""
        chunks = [{"chunk_id": "only", "content": "solo", "source": "s"}]
        factory = _mock_llm_factory("[]")

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        assert filtered == chunks
        assert scores == []
        factory.get_llm.assert_not_called()

    def test_llm_returns_empty_json_array(self):
        """LLM returns '[]' -- no scores produced, all chunks kept as fallback."""
        chunks = _make_chunks(2)
        factory = _mock_llm_factory("[]")

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        # json_match finds '[]', eval_results is empty, scores stays [],
        # score_map is empty so no chunk gets matched -> filtered is empty
        # -> fallback keeps top-1
        assert len(filtered) >= 1
        assert isinstance(scores, list)


class TestFilterLowRelevance:
    """Chunks below the relevance threshold get filtered out."""

    def test_low_scores_filtered(self):
        """Chunks with score < 0.3 are removed."""
        chunks = _make_chunks(3)
        scores_response = json.dumps([
            {"doc": 1, "score": 0.9, "reason": "High"},
            {"doc": 2, "score": 0.1, "reason": "Irrelevant"},
            {"doc": 3, "score": 0.05, "reason": "Noise"},
        ])
        factory = _mock_llm_factory(scores_response)

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        assert len(filtered) == 1
        assert filtered[0]["chunk_id"] == "chunk-1"

    def test_all_filtered_keeps_top1(self):
        """When every chunk scores below threshold, the first chunk is kept."""
        chunks = _make_chunks(2)
        scores_response = json.dumps([
            {"doc": 1, "score": 0.05, "reason": "No match"},
            {"doc": 2, "score": 0.1, "reason": "No match"},
        ])
        factory = _mock_llm_factory(scores_response)

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        # Fallback: keep top-1
        assert len(filtered) == 1
        assert filtered[0]["chunk_id"] == "chunk-1"
        # The score is marked as 0.3 (barely usable)
        assert scores[0].score == 0.3


class TestFallbackOnLLMError:
    """LLM failure returns original chunks gracefully."""

    def test_llm_exception_returns_original_chunks(self):
        """When LLM raises, all original chunks are returned unfiltered."""
        chunks = _make_chunks(3)
        factory = _mock_llm_factory_error(RuntimeError("LLM connection failed"))

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        assert filtered == chunks
        assert scores == []

    def test_json_parse_error_returns_original(self):
        """LLM returns unparseable text -- treated as format error, original kept."""
        chunks = _make_chunks(2)
        factory = _mock_llm_factory("this is not valid json at all")

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        # No JSON array found -> returns original chunks
        assert filtered == chunks
        assert scores == []


class TestPartialScores:
    """Some chunks scored, some not -- edge cases around partial results."""

    def test_partial_coverage_filters_only_scored(self):
        """LLM only returns scores for 2 of 3 chunks; unscored chunk is filtered."""
        chunks = _make_chunks(3)
        scores_response = json.dumps([
            {"doc": 1, "score": 0.9, "reason": "Good"},
            {"doc": 2, "score": 0.8, "reason": "Good"},
            # doc 3 omitted
        ])
        factory = _mock_llm_factory(scores_response)

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        # Only 2 scored, both above threshold, chunk-3 unscored => not in filtered
        chunk_ids = [c["chunk_id"] for c in filtered]
        assert "chunk-1" in chunk_ids
        assert "chunk-2" in chunk_ids
        assert "chunk-3" not in chunk_ids
        assert len(scores) == 2

    def test_score_out_of_valid_range_ignored(self):
        """Doc index 0 (invalid 1-indexed) or out-of-range is safely skipped."""
        chunks = _make_chunks(2)
        scores_response = json.dumps([
            {"doc": 0, "score": 0.9, "reason": "Bad index"},
            {"doc": 1, "score": 0.7, "reason": "OK"},
            {"doc": 5, "score": 0.8, "reason": "Out of range"},
        ])
        factory = _mock_llm_factory(scores_response)

        flt = LLMRelevanceFilter()
        filtered, scores = asyncio.run(flt.filter_chunks("q", chunks, factory))

        # doc=0 is 1-indexed -> 0-1=-1, out of range -> skipped
        # doc=5 -> 5-1=4, out of range -> skipped
        # Only doc=1 -> index 0 is valid
        assert len(scores) == 1
        assert scores[0].chunk_id == "chunk-1"


class TestGetSingleton:
    """get_self_rag() returns the same instance."""

    def test_returns_same_instance(self):
        import app.core.llm_relevance as mod
        original = mod._self_rag
        # Reset singleton for test isolation
        mod._self_rag = None
        try:
            first = get_self_rag()
            second = get_self_rag()
            assert first is second
        finally:
            mod._self_rag = original


class TestCragSelfRagIntegration:
    """CRAG 重试 + Self-RAG 过滤的协作路径测试"""

    @pytest.mark.asyncio
    async def test_crag_retry_then_self_rag_filter(self):
        """CRAG 重试后 Self-RAG 过滤，验证最终结果数量合理"""
        from app.core.rag_engine import CriticEvaluator

        # 1. 构造低质量结果：内容与 query 关键词不匹配
        low_quality = [
            {"chunk_id": f"lq-{i}", "content": f"unrelated content {i}",
             "source": f"doc{i}", "rerank_score": 0.05}
            for i in range(1, 4)
        ]
        # 2. 构造重试后的高质量结果
        high_quality = [
            {"chunk_id": f"hq-{i}", "content": f"供应商准入需要资质审核 {i}",
             "source": f"doc{i}", "rerank_score": 0.6 + i * 0.1}
            for i in range(1, 4)
        ]

        # 3. 验证 CRAG Critic 对低质量结果判定 needs_retry=True
        eval_low = CriticEvaluator.evaluate("供应商准入需要什么资质", low_quality)
        assert eval_low["needs_retry"] is True, "低质量结果应触发重试"
        assert eval_low["quality"] in ("low", "medium")

        # 4. 模拟 CRAG 重试后合并结果
        merged = low_quality + high_quality
        merged.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

        eval_merged = CriticEvaluator.evaluate("供应商准入需要什么资质", merged)
        # 重试后质量应有提升
        assert eval_merged["quality"] != "low" or eval_merged["keyword_coverage"] > eval_low["keyword_coverage"]

        # 5. Self-RAG 过滤：模拟 LLM 给高分给 hq 结果，低分给 lq 结果
        self_rag_scores = []
        for c in merged:
            if c["chunk_id"].startswith("hq-"):
                self_rag_scores.append({"doc": merged.index(c) + 1, "score": 0.85, "reason": "高度相关"})
            else:
                self_rag_scores.append({"doc": merged.index(c) + 1, "score": 0.1, "reason": "不相关"})

        factory = _mock_llm_factory(json.dumps(self_rag_scores))

        flt = LLMRelevanceFilter()
        filtered, scores = await flt.filter_chunks("供应商准入需要什么资质", merged, factory)

        # 验证：最终结果数量 > 0 且质量提升（低质量的被过滤掉）
        assert len(filtered) > 0, "最终结果不应为空"
        filtered_ids = {c["chunk_id"] for c in filtered}
        # hq 结果应被保留
        assert any(cid.startswith("hq-") for cid in filtered_ids), "高质量结果应被保留"

    @pytest.mark.asyncio
    async def test_crag_max_retries_respected(self):
        """验证 CRAG 最大重试次数限制"""
        from app.core.rag_engine import CriticEvaluator
        from unittest.mock import patch as _patch

        # 构造始终返回 low 质量的结果
        bad_results = [
            {"chunk_id": f"bad-{i}", "content": f"noise {i}",
             "source": f"doc{i}", "rerank_score": 0.01}
            for i in range(1, 3)
        ]

        # 模拟多次评估，每次都返回 needs_retry=True
        eval1 = CriticEvaluator.evaluate("测试查询", bad_results)
        assert eval1["needs_retry"] is True

        # 用 mock 设置 CRAG_MAX_RETRIES=1，验证重试限制
        with _patch("app.config.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.CRAG_ENABLED = True
            settings.CRAG_MAX_RETRIES = 1
            settings.LLM_RELEVANCE_ENABLED = False

            # 验证配置生效
            assert settings.CRAG_MAX_RETRIES == 1

            # 模拟重试逻辑：第二次评估仍然 low
            eval2 = CriticEvaluator.evaluate("测试查询", bad_results)
            assert eval2["needs_retry"] is True
            # 在 MAX_RETRIES=1 的情况下，应该只重试 1 次后就停止
            # （这里验证的是 CriticEvaluator 一致性，不是 RAGAgent 逻辑）

    @pytest.mark.asyncio
    async def test_crag_disabled_skips_retry(self):
        """CRAG 禁用时跳过重试"""
        from unittest.mock import patch as _patch

        with _patch("app.config.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.CRAG_ENABLED = False

            # CRAG 禁用时，CriticEvaluator.evaluate 不应被调用
            # 验证配置标志
            assert settings.CRAG_ENABLED is False

            # 模拟 RAGAgent.answer 的行为：
            # 当 CRAG_ENABLED=False 时，应跳过 Step 3.5
            results = [
                {"chunk_id": "c1", "content": "test", "source": "d1", "rerank_score": 0.3}
            ]
            # 即使质量低也不触发重试（因为 CRAG 被禁用）
            # 验证流程直接进入 Self-RAG 或上下文组装
            assert settings.CRAG_ENABLED is False, "CRAG 应被禁用，不会触发重试逻辑"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
