"""Reranker 引擎单元测试 — 之前 0 覆盖"""
import pytest


class TestRerankerEngine:
    def test_init_loads_model(self):
        """init 实际行为：环境有 sentence-transformers 时加载 CrossEncoder"""
        from app.core.rag.reranker import RerankerEngine
        engine = RerankerEngine()
        engine.init()
        if engine._model is None:
            pytest.skip("CrossEncoder 不可用，跳过")
        assert engine._model is not None

    def test_rerank_empty_documents(self):
        """空文档列表直接返回空，不触发 model"""
        from app.core.rag.reranker import RerankerEngine
        engine = RerankerEngine()
        assert engine.rerank("query", [], top_k=3) == []

    def test_rerank_without_model_falls_back_to_bm25_score(self, monkeypatch):
        """model 不可用时降级为按 bm25_score 排序"""
        from app.core.rag.reranker import RerankerEngine
        engine = RerankerEngine()
        monkeypatch.setattr(engine, "init", lambda: None)
        object.__setattr__(engine, "_model", None)

        docs = [
            {"content": "C", "bm25_score": 0.3},
            {"content": "A", "bm25_score": 0.9},
            {"content": "B", "bm25_score": 0.6},
        ]
        result = engine.rerank("query", docs, top_k=3)
        # 应按 bm25_score 降序
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "B"
        assert result[2]["content"] == "C"
        # 标记降级分数
        assert all("rerank_score" in d for d in result)

    def test_rerank_fallback_uses_vector_score_when_no_bm25(self, monkeypatch):
        """降级路径：无 bm25_score 时用 vector_score"""
        from app.core.rag.reranker import RerankerEngine
        engine = RerankerEngine()
        monkeypatch.setattr(engine, "init", lambda: None)
        object.__setattr__(engine, "_model", None)

        docs = [
            {"content": "low", "vector_score": 0.1},
            {"content": "high", "vector_score": 0.9},
        ]
        result = engine.rerank("query", docs, top_k=2)
        assert result[0]["content"] == "high"
        assert result[0]["rerank_score"] == 0.9

    def test_rerank_fallback_score_zero_when_no_scores(self, monkeypatch):
        """降级路径：完全没分数 → 视为 0"""
        from app.core.rag.reranker import RerankerEngine
        engine = RerankerEngine()
        monkeypatch.setattr(engine, "init", lambda: None)
        object.__setattr__(engine, "_model", None)

        docs = [
            {"content": "no-score-1"},
            {"content": "no-score-2"},
        ]
        result = engine.rerank("query", docs, top_k=2)
        # 两个都 0 分，排序稳定
        assert all(d["rerank_score"] == 0.0 for d in result)

    def test_rerank_with_mock_model(self, monkeypatch):
        """mock model.predict → 验证正常路径"""
        from app.core.rag.reranker import RerankerEngine

        # mock model：第一个 doc 分数最高
        class MockModel:
            def predict(self, pairs):
                return [0.9, 0.1, 0.5]  # 对应 3 个文档的分数

        engine = RerankerEngine()
        object.__setattr__(engine, "_model", MockModel())

        docs = [
            {"content": "doc1 content"},
            {"content": "doc2 content"},
            {"content": "doc3 content"},
        ]
        result = engine.rerank("query", docs, top_k=2)
        # 应按分数降序，top_k=2 → 前 2 个
        assert len(result) == 2
        assert result[0]["content"] == "doc1 content"  # 0.9
        assert result[0]["rerank_score"] == 0.9
        assert result[1]["content"] == "doc3 content"  # 0.5

    def test_rerank_top_k_limit(self, monkeypatch):
        """top_k 应严格限制返回数量"""
        from app.core.rag.reranker import RerankerEngine

        class MockModel:
            def predict(self, pairs):
                return [float(i) for i in range(len(pairs))]  # 0, 1, 2, 3, 4

        engine = RerankerEngine()
        object.__setattr__(engine, "_model", MockModel())

        docs = [{"content": f"doc{i}"} for i in range(5)]
        result = engine.rerank("query", docs, top_k=2)
        assert len(result) == 2
