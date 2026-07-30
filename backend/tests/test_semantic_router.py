"""
SupplyChainRAG - SemanticRouter Unit Tests

Tests the semantic routing module that uses embedding cosine similarity
to classify user intents without LLM calls.

All embedding operations are mocked -- no external services needed.
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.semantic_router import SemanticRouter, SemanticRoute, get_semantic_router


# ---- helpers ----

def _make_embedding(dim: int = 8, seed: int = 42) -> list[float]:
    """Generate a deterministic pseudo-random unit embedding."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(float)
    return (vec / np.linalg.norm(vec)).tolist()


def _build_initialized_router(
    intent_vectors: dict[str, list[list[float]]] | None = None,
) -> SemanticRouter:
    """Build a SemanticRouter with pre-populated route embeddings."""
    router = SemanticRouter()
    if intent_vectors is None:
        intent_vectors = {
            "rag_answer": [_make_embedding(seed=1), _make_embedding(seed=2)],
            "tool_call": [_make_embedding(seed=10), _make_embedding(seed=11)],
            "greeting": [_make_embedding(seed=20)],
        }
    router._route_embeddings = {
        intent: [np.array(v) for v in vecs]
        for intent, vecs in intent_vectors.items()
    }
    router._initialized = True
    return router


# ---- Cosine Similarity ----

class TestCosineSimilarity:
    """Tests for SemanticRouter._cosine_similarity (static method)."""

    def test_identical_vectors_return_1(self):
        """Identical vectors should have cosine similarity of 1.0."""
        a = np.array([1.0, 0.0, 0.0])
        assert SemanticRouter._cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_0(self):
        """Orthogonal vectors should have cosine similarity of 0.0."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert SemanticRouter._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors_return_neg1(self):
        """Opposite vectors should have cosine similarity of -1.0."""
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        assert SemanticRouter._cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_returns_0(self):
        """Zero vector should yield 0.0 (division-by-zero guard)."""
        a = np.array([0.0, 0.0])
        b = np.array([1.0, 0.0])
        assert SemanticRouter._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_both_zero_vectors_return_0(self):
        """Both zero vectors should yield 0.0."""
        a = np.array([0.0, 0.0])
        assert SemanticRouter._cosine_similarity(a, a) == pytest.approx(0.0)

    def test_non_unit_vectors_normalized(self):
        """Cosine similarity is scale-invariant."""
        a = np.array([3.0, 4.0])
        b = np.array([6.0, 8.0])
        assert SemanticRouter._cosine_similarity(a, b) == pytest.approx(1.0)


# ---- Initialization ----

class TestInitialization:
    """Tests for SemanticRouter.init()."""

    @staticmethod
    def _expected_examples() -> dict[str, list[str]]:
        """init() 实际加载的样本：优先 intent_routes.json，兜底 ROUTE_EXAMPLES"""
        from app.core.intent_routes import get_intent_routes
        cfg = get_intent_routes()
        if cfg.semantic_routes:
            return {i: r["utterances"] for i, r in cfg.semantic_routes.items()}
        return dict(SemanticRouter.ROUTE_EXAMPLES)

    def test_init_populates_route_embeddings(self):
        """init() should call embed_query for each example and store embeddings."""
        router = SemanticRouter()
        mock_engine = MagicMock()
        mock_engine.embed_query.return_value = _make_embedding(dim=8, seed=0)

        router.init(mock_engine)

        expected = self._expected_examples()
        assert router._initialized is True
        for intent in expected:
            assert intent in router._route_embeddings
        assert mock_engine.embed_query.call_count == sum(
            len(v) for v in expected.values()
        )

    def test_init_is_idempotent(self):
        """Second call to init() should be a no-op."""
        router = SemanticRouter()
        mock_engine = MagicMock()
        mock_engine.embed_query.return_value = _make_embedding(dim=8, seed=0)

        router.init(mock_engine)
        first_call_count = mock_engine.embed_query.call_count

        router.init(mock_engine)
        assert mock_engine.embed_query.call_count == first_call_count

    def test_init_failure_sets_not_initialized(self):
        """If embed_query raises, router should remain uninitialized."""
        router = SemanticRouter()
        mock_engine = MagicMock()
        mock_engine.embed_query.side_effect = RuntimeError("model offline")

        router.init(mock_engine)

        assert router._initialized is False
        assert len(router._route_embeddings) == 0


# ---- Routing ----

class TestRouting:
    """Tests for SemanticRouter.route()."""

    def test_route_returns_none_when_uninitialized(self):
        """route() should return None if init() was never called."""
        router = SemanticRouter()
        result = router.route(_make_embedding(seed=99))
        assert result is None

    def test_route_high_similarity_returns_intent(self):
        """Query embedding identical to a stored greeting should match 'greeting'."""
        greeting_vec = np.array(_make_embedding(dim=8, seed=20))
        router = SemanticRouter()
        router._route_embeddings = {"greeting": [greeting_vec]}
        router._initialized = True

        result = router.route(greeting_vec.tolist())

        assert result is not None
        assert result.intent == "greeting"
        assert result.confidence == pytest.approx(1.0)
        assert result.method == "semantic"

    def test_route_below_threshold_returns_none(self):
        """When best similarity < SIMILARITY_THRESHOLD, route() returns None."""
        # Store a vector pointing one direction
        router = SemanticRouter()
        stored = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        router._route_embeddings = {"rag_answer": [stored]}
        router._initialized = True

        # Query vector orthogonal to stored → similarity = 0
        query = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        result = router.route(query.tolist())

        assert result is None

    def test_route_picks_best_intent(self):
        """When multiple intents are stored, route() picks the closest one."""
        dim = 8
        # rag_answer points along axis 0
        rag_vec = np.zeros(dim)
        rag_vec[0] = 1.0
        # tool_call points along axis 1
        tool_vec = np.zeros(dim)
        tool_vec[1] = 1.0

        router = SemanticRouter()
        router._route_embeddings = {
            "rag_answer": [rag_vec],
            "tool_call": [tool_vec],
        }
        router._initialized = True

        # Query aligned with tool_call
        query = np.zeros(dim)
        query[1] = 1.0
        result = router.route(query.tolist())

        assert result is not None
        assert result.intent == "tool_call"
        assert result.confidence == pytest.approx(1.0)

    def test_route_ambiguous_margin_returns_none(self):
        """top1-top2 分差小于 SEMANTIC_ROUTER_MARGIN → 模糊，回退 LLM（返回 None）"""
        # 两个意图向量与 query 的相似度分别为 1.0 和 0.99，margin=0.01 < 0.03
        a = np.array([1.0, 0.0])
        close = 0.99
        b = np.array([close, np.sqrt(1 - close ** 2)])

        router = SemanticRouter()
        router._route_embeddings = {
            "rag_answer": [a],
            "tool_call": [b],
        }
        router._initialized = True

        result = router.route(a.tolist())
        assert result is None

    def test_route_per_intent_threshold_override(self):
        """per-intent 阈值覆盖全局阈值：分数高于全局但低于意图阈值 → None"""
        router = SemanticRouter()
        # 相似度 0.8：高于全局 0.65，低于 per-intent 0.9
        dot = 0.8
        stored = np.array([dot, np.sqrt(1 - dot ** 2)])
        router._route_embeddings = {"greeting": [stored]}
        router._thresholds = {"greeting": 0.9}
        router._initialized = True

        result = router.route([1.0, 0.0])
        assert result is None

        # 去掉 per-intent 阈值后，全局阈值 0.65 应命中
        router._thresholds = {}
        result = router.route([1.0, 0.0])
        assert result is not None
        assert result.intent == "greeting"

    def test_reload_rebuilds_embeddings(self):
        """reload() 重建路由 embedding（配置更新后调用）"""
        router = SemanticRouter()
        mock_engine = MagicMock()
        mock_engine.embed_query.return_value = _make_embedding(dim=8, seed=0)

        router.init(mock_engine)
        assert router._initialized is True
        first_count = mock_engine.embed_query.call_count

        ok = router.reload()
        assert ok is True
        assert router._initialized is True
        # reload 重新计算了全部样本 embedding
        assert mock_engine.embed_query.call_count == first_count * 2

    def test_reload_without_engine_returns_false(self):
        """未 init 且未传 engine 时 reload 应返回 False"""
        router = SemanticRouter()
        assert router.reload() is False
        assert router._initialized is False

    def test_route_exactly_at_threshold(self):
        """Score exactly at SIMILARITY_THRESHOLD should match."""
        router = SemanticRouter()
        threshold = SemanticRouter.SIMILARITY_THRESHOLD

        # Create a vector pair with known cosine similarity = threshold
        # Use two unit vectors with dot product = threshold
        a = np.array([1.0, 0.0])
        b = np.array([threshold, np.sqrt(1 - threshold ** 2)])
        router._route_embeddings = {"greeting": [b]}
        router._initialized = True

        result = router.route(a.tolist())

        assert result is not None
        assert result.intent == "greeting"
        assert result.confidence == pytest.approx(threshold, abs=1e-6)

    def test_route_just_below_threshold(self):
        """Score just below SIMILARITY_THRESHOLD should return None."""
        router = SemanticRouter()
        threshold = SemanticRouter.SIMILARITY_THRESHOLD
        eps = 0.001

        a = np.array([1.0, 0.0])
        dot_target = threshold - eps
        b = np.array([dot_target, np.sqrt(1 - dot_target ** 2)])
        router._route_embeddings = {"greeting": [b]}
        router._initialized = True

        result = router.route(a.tolist())

        assert result is None

    def test_route_all_intent_categories(self):
        """Each intent category should be reachable with a matching vector."""
        dim = 8
        intents = ["rag_answer", "tool_call", "greeting"]

        for idx, intent in enumerate(intents):
            vec = np.zeros(dim)
            vec[idx] = 1.0

            router = SemanticRouter()
            router._route_embeddings = {intent: [vec]}
            router._initialized = True

            result = router.route(vec.tolist())
            assert result is not None, f"Intent '{intent}' should be reachable"
            assert result.intent == intent


# ---- Edge Cases ----

class TestEdgeCases:
    """Edge-case scenarios for semantic routing."""

    def test_empty_route_embeddings(self):
        """Router with no embeddings should return None."""
        router = SemanticRouter()
        router._route_embeddings = {}
        router._initialized = True

        result = router.route(_make_embedding(seed=42))
        assert result is None

    def test_very_short_embedding(self):
        """A single-dimension embedding should still work."""
        router = SemanticRouter()
        router._route_embeddings = {"greeting": [np.array([1.0])]}
        router._initialized = True

        result = router.route([1.0])
        assert result is not None
        assert result.intent == "greeting"

    def test_high_dimensional_embedding(self):
        """Standard 1536-dim embedding should work without issues."""
        dim = 1536
        vec = np.zeros(dim)
        vec[0] = 1.0

        router = SemanticRouter()
        router._route_embeddings = {"rag_answer": [vec]}
        router._initialized = True

        query = np.zeros(dim)
        query[0] = 1.0
        result = router.route(query.tolist())

        assert result is not None
        assert result.confidence == pytest.approx(1.0)


# ---- Singleton ----

class TestSingleton:
    """Tests for get_semantic_router() singleton."""

    def test_singleton_returns_same_instance(self):
        """get_semantic_router() should return the same object."""
        with patch("app.core.semantic_router._semantic_router", None):
            r1 = get_semantic_router()
            r2 = get_semantic_router()
            assert r1 is r2
            assert isinstance(r1, SemanticRouter)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
