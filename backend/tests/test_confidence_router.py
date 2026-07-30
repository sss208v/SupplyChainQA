"""
SupplyChainRAG - ConfidenceRouter Unit Tests

Tests the three-tier confidence routing strategy:
- High confidence (>0.7) -> direct answer
- Medium confidence (0.3-0.7) -> query rewrite
- Low confidence (<0.3) -> web search fallback

All LLM and HTTP calls are mocked -- no external services needed.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.confidence_router import (
    ConfidenceRouter,
    ConfidenceDecision,
    WebSearchResult,
    get_confidence_router,
)


# ---- helpers ----

def _mock_llm_factory(response_text: str) -> MagicMock:
    """Build a mock llm_factory whose get_llm().ainvoke() returns response_text."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content=response_text)
    factory = MagicMock()
    factory.get_llm.return_value = mock_llm
    return factory


def _mock_llm_factory_error(exc: Exception) -> MagicMock:
    """Build a mock llm_factory that raises on ainvoke."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = exc
    factory = MagicMock()
    factory.get_llm.return_value = mock_llm
    return factory


# ---- Confidence Decision ----

class TestDecide:
    """Tests for ConfidenceRouter.decide()."""

    def test_high_confidence_returns_direct(self):
        """Confidence > 0.7 should return 'high' tier with 'direct' strategy."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.85, query="test")

        assert isinstance(result, ConfidenceDecision)
        assert result.tier == "high"
        assert result.strategy == "direct"
        assert result.confidence == 0.85

    def test_medium_confidence_returns_rewrite(self):
        """Confidence in [0.3, 0.7) should return 'medium' tier with 'rewrite' strategy."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.5, query="test")

        assert result.tier == "medium"
        assert result.strategy == "rewrite"
        assert result.confidence == 0.5

    def test_low_confidence_returns_web_search(self):
        """Confidence < 0.3 should return 'low' tier with 'web_search' strategy."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.1, query="test")

        assert result.tier == "low"
        assert result.strategy == "web_search"
        assert result.confidence == 0.1

    def test_exactly_at_high_threshold(self):
        """Confidence == 0.7 should be 'high' (>= boundary)."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.7, query="test")

        assert result.tier == "high"
        assert result.strategy == "direct"

    def test_just_below_high_threshold(self):
        """Confidence == 0.699 should be 'medium'."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.699, query="test")

        assert result.tier == "medium"
        assert result.strategy == "rewrite"

    def test_exactly_at_low_threshold(self):
        """Confidence == 0.3 should be 'medium' (>= LOW_THRESHOLD)."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.3, query="test")

        assert result.tier == "medium"
        assert result.strategy == "rewrite"

    def test_just_below_low_threshold(self):
        """Confidence == 0.299 should be 'low'."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.299, query="test")

        assert result.tier == "low"
        assert result.strategy == "web_search"

    def test_score_zero(self):
        """Confidence == 0.0 should be 'low'."""
        router = ConfidenceRouter()
        result = router.decide(confidence=0.0, query="test")

        assert result.tier == "low"
        assert result.strategy == "web_search"
        assert result.confidence == 0.0

    def test_score_one(self):
        """Confidence == 1.0 should be 'high'."""
        router = ConfidenceRouter()
        result = router.decide(confidence=1.0, query="test")

        assert result.tier == "high"
        assert result.strategy == "direct"
        assert result.confidence == 1.0

    def test_description_present_for_each_tier(self):
        """Each tier should carry a non-empty description."""
        router = ConfidenceRouter()
        for score in [0.1, 0.5, 0.9]:
            result = router.decide(confidence=score, query="test")
            assert result.description, f"Description empty for score={score}"


# ---- Query Rewrite ----

class TestRewriteQuery:
    """Tests for ConfidenceRouter.rewrite_query()."""

    @pytest.mark.asyncio
    async def test_rewrite_returns_multiple_variants(self):
        """LLM returning newline-separated rewrites should produce a list."""
        rewrites = "供应商准入的资质要求有哪些\n供应商需要什么资格才能准入\n准入供应商需要哪些资质"
        factory = _mock_llm_factory(rewrites)
        router = ConfidenceRouter()

        result = await router.rewrite_query("供应商准入资质", factory)

        assert isinstance(result, list)
        assert len(result) == 3
        assert "供应商准入的资质要求有哪些" in result

    @pytest.mark.asyncio
    async def test_rewrite_filters_empty_lines(self):
        """Empty lines and whitespace-only lines should be filtered out."""
        rewrites = "改写版本一\n\n   \n改写版本二"
        factory = _mock_llm_factory(rewrites)
        router = ConfidenceRouter()

        result = await router.rewrite_query("test", factory)

        assert result == ["改写版本一", "改写版本二"]

    @pytest.mark.asyncio
    async def test_rewrite_excludes_original_query(self):
        """If LLM echoes the original query, it should be excluded."""
        original = "安全库存公式"
        rewrites = f"安全库存的计算公式是什么\n{original}\n库存安全量怎么算"
        factory = _mock_llm_factory(rewrites)
        router = ConfidenceRouter()

        result = await router.rewrite_query(original, factory)

        assert original not in result
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_rewrite_caps_at_three(self):
        """More than 3 rewrites should be truncated to 3."""
        rewrites = "版本一\n版本二\n版本三\n版本四\n版本五"
        factory = _mock_llm_factory(rewrites)
        router = ConfidenceRouter()

        result = await router.rewrite_query("test", factory)

        assert len(result) <= 3

    @pytest.mark.asyncio
    async def test_rewrite_llm_error_returns_empty(self):
        """When LLM raises, rewrite_query should return an empty list."""
        factory = _mock_llm_factory_error(RuntimeError("LLM timeout"))
        router = ConfidenceRouter()

        result = await router.rewrite_query("test", factory)

        assert result == []

    @pytest.mark.asyncio
    async def test_rewrite_calls_llm_with_correct_params(self):
        """rewrite_query should call get_llm with temperature=0.3, streaming=False."""
        factory = _mock_llm_factory("改写一\n改写二")
        router = ConfidenceRouter()

        await router.rewrite_query("test query", factory)

        factory.get_llm.assert_called_once_with(temperature=0.3, streaming=False)


# ---- Format Web Results ----

class TestFormatWebResults:
    """Tests for ConfidenceRouter.format_web_results_for_context()."""

    def test_format_empty_results(self):
        """Empty list should return empty string."""
        router = ConfidenceRouter()
        assert router.format_web_results_for_context([]) == ""

    def test_format_single_result(self):
        """Single result should include title and snippet."""
        router = ConfidenceRouter()
        results = [WebSearchResult(title="Test Title", link="https://example.com", snippet="Test snippet")]
        output = router.format_web_results_for_context(results)

        assert "Test Title" in output
        assert "Test snippet" in output
        assert "https://example.com" in output

    def test_format_multiple_results(self):
        """Multiple results should be numbered sequentially."""
        router = ConfidenceRouter()
        results = [
            WebSearchResult(title="First", link="", snippet="S1"),
            WebSearchResult(title="Second", link="", snippet="S2"),
        ]
        output = router.format_web_results_for_context(results)

        assert "[Web-1]" in output
        assert "[Web-2]" in output


# ---- Singleton ----

class TestSingleton:
    """Tests for get_confidence_router() singleton."""

    def test_singleton_returns_same_instance(self):
        """get_confidence_router() should return the same object."""
        with patch("app.core.confidence_router._confidence_router", None):
            r1 = get_confidence_router()
            r2 = get_confidence_router()
            assert r1 is r2
            assert isinstance(r1, ConfidenceRouter)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
