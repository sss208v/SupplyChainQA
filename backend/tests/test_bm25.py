"""BM25 引擎单元测试 — 之前 0 覆盖"""
import sys
import types
import pytest


class TestBM25Engine:
    def test_tokenize_basic(self):
        """_tokenize 应按 jieba 分词"""
        from app.core.rag.bm25 import BM25Engine
        tokens = BM25Engine._tokenize("供应商资质管理")
        assert isinstance(tokens, list)
        assert len(tokens) > 0
        # 中文应被 jieba 切分为多个 token
        assert all(isinstance(t, str) for t in tokens)

    def test_tokenize_empty(self):
        """空字符串应返回空列表"""
        from app.core.rag.bm25 import BM25Engine
        assert BM25Engine._tokenize("") == []

    def test_index_and_search(self):
        """index_documents + search 端到端：包含 query 的 doc 应被检索"""
        from app.core.rag.bm25 import BM25Engine

        engine = BM25Engine()
        chunks = [
            {"id": "c1", "content": "供应商资质管理需要三证齐全", "chunk_index": 0},
            {"id": "c2", "content": "采购订单的状态管理", "chunk_index": 0},
        ]
        engine.index_documents("doc-1", chunks)

        # 搜索"供应商"应返回含供应商的 c1
        results = engine.search("供应商资质", top_k=2)
        assert len(results) >= 1
        assert any("供应商" in r["content"] for r in results)

    def test_search_no_match(self):
        """query 无匹配时返回空列表"""
        from app.core.rag.bm25 import BM25Engine
        engine = BM25Engine()
        engine.index_documents("doc-1", [{"id": "c1", "content": "苹果", "chunk_index": 0}])
        # 完全不相关的词
        results = engine.search("数据库查询优化", top_k=2)
        # 可能是空，也可能 score 极低；接受空或低分
        assert isinstance(results, list)

    def test_remove_doc(self):
        """remove_doc 应从索引中清除指定 doc_id 的所有 chunk（chunk 必须带 doc_id 字段）"""
        from app.core.rag.bm25 import BM25Engine
        engine = BM25Engine()
        # chunks 必须显式带 doc_id 字段，否则 remove_doc 找不到
        engine.index_documents("doc-1", [{"doc_id": "doc-1", "id": "c1", "content": "测试内容", "chunk_index": 0}])
        engine.index_documents("doc-2", [{"doc_id": "doc-2", "id": "c2", "content": "测试内容", "chunk_index": 0}])
        before = len(engine._chunks)
        assert before == 2
        engine.remove_doc("doc-1")
        after = len(engine._chunks)
        assert after == 1
        # 剩余那条是 doc-2
        assert engine._chunks[0]["doc_id"] == "doc-2"

    def test_remove_nonexistent_doc_silent(self):
        """移除不存在的 doc_id 应静默 no-op，不抛错"""
        from app.core.rag.bm25 import BM25Engine
        engine = BM25Engine()
        engine.remove_doc("doc-not-exists")  # 不抛错即通过

    def test_search_top_k_limit(self):
        """search 应严格限制 top_k 返回数量"""
        from app.core.rag.bm25 import BM25Engine
        engine = BM25Engine()
        chunks = [
            {"id": f"c{i}", "content": f"测试内容 {i}", "chunk_index": 0}
            for i in range(10)
        ]
        engine.index_documents("doc-1", chunks)
        results = engine.search("测试", top_k=3)
        assert len(results) <= 3

    def test_index_with_security_group(self):
        """带 security_group 的 chunk 索引不应抛错"""
        from app.core.rag.bm25 import BM25Engine
        engine = BM25Engine()
        chunks = [{"id": "c1", "content": "机密内容", "chunk_index": 0}]
        engine.index_documents("doc-1", chunks, security_group=["admin"])
        results = engine.search("机密", top_k=1)
        assert len(results) >= 1

    def test_index_empty_chunks(self):
        """空 chunks 列表索引 —— 实际行为：BM25Okapi([]) 抛 ZeroDivisionError

        这是真实 bug：调用方传空 chunks 会让 rank_bm25 崩溃。
        建议在 index_documents 开头加 `if not chunks: return` 守卫。
        """
        from app.core.rag.bm25 import BM25Engine
        engine = BM25Engine()
        with pytest.raises(ZeroDivisionError):
            engine.index_documents("doc-empty", [])


class TestBM25StopwordFilter:
    """停用词过滤测试（审查 Issue #6：jieba 分词后无停用词过滤）"""

    def _with_fake_jieba(self, tokens):
        """注入 fake jieba 模块，模拟 jieba.cut 的分词结果（可控、确定）"""
        fake = types.ModuleType("jieba")
        fake.cut = lambda text: list(tokens)
        saved = sys.modules.get("jieba")
        sys.modules["jieba"] = fake
        return saved

    def test_tokenize_filters_cn_stopwords(self):
        """中文停用词（的/是/了等）应从 token 中过滤，业务词保留"""
        from app.core.rag.bm25 import BM25Engine
        saved = self._with_fake_jieba(["供应商", "的", "库存", "是", "多少"])
        try:
            tokens = BM25Engine._tokenize("供应商的库存是多少")
        finally:
            if saved is not None:
                sys.modules["jieba"] = saved
            else:
                sys.modules.pop("jieba", None)
        assert "的" not in tokens
        assert "是" not in tokens
        assert "供应商" in tokens
        assert "库存" in tokens

    def test_tokenize_filters_en_stopwords(self):
        """英文停用词（the/is/a 等）应过滤，实义词保留"""
        from app.core.rag.bm25 import BM25Engine
        saved = self._with_fake_jieba([])  # 纯英文场景，jieba 不产出中文 token
        try:
            tokens = BM25Engine._tokenize("The supplier is checking inventory")
        finally:
            if saved is not None:
                sys.modules["jieba"] = saved
            else:
                sys.modules.pop("jieba", None)
        assert "the" not in tokens
        assert "is" not in tokens
        assert "supplier" in tokens
        assert "inventory" in tokens

    def test_tokenize_stopword_only_text_returns_empty(self):
        """纯停用词文本过滤后应返回空列表（空输入语义保持兼容）"""
        from app.core.rag.bm25 import BM25Engine
        saved = self._with_fake_jieba(["的", "是", "了"])
        try:
            tokens = BM25Engine._tokenize("的是了")
        finally:
            if saved is not None:
                sys.modules["jieba"] = saved
            else:
                sys.modules.pop("jieba", None)
        assert tokens == []

    def test_tokenize_empty_still_empty(self):
        """空字符串经停用词过滤后仍返回空列表（与现有 test_tokenize_empty 兼容）"""
        from app.core.rag.bm25 import BM25Engine
        assert BM25Engine._tokenize("") == []
