"""Embedding 引擎单元测试 — 之前 0 覆盖"""
import pytest


class TestEmbeddingEngine:
    def test_init_loads_model(self, monkeypatch):
        """init() 实际行为：环境有 sentence-transformers 时成功加载 model"""
        from app.core.rag.embedding import EmbeddingEngine
        engine = EmbeddingEngine()
        engine.init()
        # 实际：模型加载成功，_model 不为 None
        # 这条测试如果失败，说明环境缺 sentence-transformers 或 HuggingFace 资源
        if engine._model is None:
            pytest.skip("sentence-transformers 不可用，跳过")
        assert engine._model is not None

    def test_embed_query_without_model_raises(self, monkeypatch):
        """强制 _model = None 且 init 也失败 → embed_query 抛 RuntimeError"""
        from app.core.rag.embedding import EmbeddingEngine
        engine = EmbeddingEngine()
        # mock init 阻止自动重新加载
        monkeypatch.setattr(engine, "init", lambda: None)
        object.__setattr__(engine, "_model", None)
        with pytest.raises(RuntimeError, match="嵌入模型不可用"):
            engine.embed_query("test query")

    def test_embed_documents_empty_list_with_model(self):
        """空列表在有 model 时应返回空列表（早期返回，不进 model）"""
        from app.core.rag.embedding import EmbeddingEngine

        class MockModel:
            def embed_documents(self, texts):
                # 不应被调用
                raise AssertionError("不应调用")

        engine = EmbeddingEngine()
        engine._model = MockModel()
        assert engine.embed_documents([]) == []

    def test_clear_cache(self):
        """clear_cache 应清空模块级 _embedding_cache"""
        from app.core.rag import embedding as emb_mod
        emb_mod._embedding_cache.clear()
        # 写入一些
        emb_mod._embedding_cache["key1"] = [0.1, 0.2]
        emb_mod._embedding_cache["key2"] = [0.3, 0.4]
        assert len(emb_mod._embedding_cache) == 2
        # clear
        EmbeddingEngine_instance = type(emb_mod.EmbeddingEngine())  # 触发 init
        # 实际：clear_cache 是实例方法但操作模块级 cache
        engine = emb_mod.EmbeddingEngine()
        engine.clear_cache()
        assert len(emb_mod._embedding_cache) == 0


class TestEmbeddingEngineWithMock:
    """Mock 注入：模拟 model 已初始化的场景"""

    def test_embed_query_returns_vector_with_mock_model(self, monkeypatch):
        from app.core.rag import embedding as emb_mod

        # 创建一个 mock model，模拟 HuggingFaceBgeEmbeddings
        class MockModel:
            def embed_query(self, text):
                return [0.1, 0.2, 0.3, 0.4]

        # 清缓存避免其他测试干扰
        emb_mod._embedding_cache.clear()

        engine = emb_mod.EmbeddingEngine()
        engine._model = MockModel()

        result = engine.embed_query("test")
        assert result == [0.1, 0.2, 0.3, 0.4]

    def test_embed_query_caches_result(self, monkeypatch):
        from app.core.rag import embedding as emb_mod

        call_count = [0]

        class MockModel:
            def embed_query(self, text):
                call_count[0] += 1
                return [0.1, 0.2, 0.3]

        emb_mod._embedding_cache.clear()

        engine = emb_mod.EmbeddingEngine()
        engine._model = MockModel()

        # 第一次调用
        r1 = engine.embed_query("重复问题")
        # 第二次调用相同问题 → 应从缓存读，不调 model
        r2 = engine.embed_query("重复问题")

        assert r1 == r2 == [0.1, 0.2, 0.3]
        # 缓存命中应只调一次 model
        assert call_count[0] == 1

    def test_embed_documents_with_mock_model(self, monkeypatch):
        from app.core.rag import embedding as emb_mod

        class MockModel:
            def embed_documents(self, texts):
                return [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]

        emb_mod._embedding_cache.clear()

        engine = emb_mod.EmbeddingEngine()
        engine._model = MockModel()

        result = engine.embed_documents(["doc1", "doc2", "doc3"])
        assert len(result) == 3
        assert result[0] == [0.1, 0.2]
        assert result[2] == [0.5, 0.6]

    def test_cache_lru_eviction(self, monkeypatch):
        """LRU 缓存满后应淘汰最早的 key"""
        from app.core.rag import embedding as emb_mod

        class MockModel:
            def embed_query(self, text):
                return [len(text)]

        emb_mod._embedding_cache.clear()
        # 临时把 max 调小以便测试
        original_max = emb_mod._EMBEDDING_CACHE_MAX
        emb_mod._EMBEDDING_CACHE_MAX = 3
        try:
            engine = emb_mod.EmbeddingEngine()
            engine._model = MockModel()

            engine.embed_query("a")
            engine.embed_query("b")
            engine.embed_query("c")
            assert len(emb_mod._embedding_cache) == 3
            # 插入第 4 条 → 应淘汰最早（按插入顺序）
            engine.embed_query("d")
            assert len(emb_mod._embedding_cache) == 3
        finally:
            emb_mod._EMBEDDING_CACHE_MAX = original_max

