"""
RAGAgent 单元测试 — 查询分类 / 多查询准备 / 上下文格式化 / 端到端回答
所有外部依赖（LLM、RAG引擎、Redis、Self-RAG）均使用 mock。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def rag_agent():
    with patch("app.agents.rag.rag_engine"), patch("app.agents.rag.chat_memory", None):
        from app.agents.rag import RAGAgent
        agent = RAGAgent()
        yield agent


def _chunk(idx, score=0.8, source="doc.pdf"):
    return {"chunk_id": f"c{idx}", "content": f"内容{idx}", "source": source,
            "page_num": 1, "chunk_index": idx, "section_title": f"章节{idx}",
            "rerank_score": score}


def _llm_resp(content):
    r = MagicMock(); r.content = content; return r


def _mock_analysis():
    a = MagicMock()
    a.complexity, a.strategy = 0.3, "light"
    a.entity_count, a.needs_reasoning, a.method = 1, False, "direct"
    return a


# ---- _classify_query ----

class TestClassifyQuery:
    def test_short_query_broad(self, rag_agent):
        assert rag_agent._classify_query("AI") == "broad"

    def test_short_with_keyword_not_broad(self, rag_agent):
        assert rag_agent._classify_query("什么是AI") in ("specific", "ambiguous")

    def test_multi_question_marks_broad(self, rag_agent):
        assert rag_agent._classify_query("怎么用？还有呢？") == "broad"

    def test_tech_keyword_specific(self, rag_agent):
        assert rag_agent._classify_query("如何配置API参数") == "specific"

    def test_default_ambiguous(self, rag_agent):
        assert rag_agent._classify_query("向量数据库的基本原理") == "ambiguous"

    def test_deploy_keyword_specific(self, rag_agent):
        assert rag_agent._classify_query("部署代码报错了") == "specific"


# ---- _prepare_queries ----

class TestPrepareQueries:
    @pytest.mark.asyncio
    async def test_specific_returns_original(self, rag_agent):
        assert await rag_agent._prepare_queries("创建索引", "specific") == ["创建索引"]

    @pytest.mark.asyncio
    async def test_ambiguous_hyde(self, rag_agent):
        m = AsyncMock(); m.ainvoke.return_value = _llm_resp("假设文档")
        with patch("app.agents.rag.LLMFactory") as mf:
            mf.get_llm.return_value = m
            r = await rag_agent._prepare_queries("数据库原理", "ambiguous")
        assert r == ["假设文档"]

    @pytest.mark.asyncio
    async def test_broad_sub_queries(self, rag_agent):
        m = AsyncMock(); m.ainvoke.return_value = _llm_resp("问题A\n问题B\n问题C")
        with patch("app.agents.rag.LLMFactory") as mf:
            mf.get_llm.return_value = m
            r = await rag_agent._prepare_queries("讲讲AI", "broad")
        assert len(r) == 3

    @pytest.mark.asyncio
    async def test_broad_max_5(self, rag_agent):
        m = AsyncMock()
        m.ainvoke.return_value = _llm_resp("\n".join(f"Q{i}" for i in range(8)))
        with patch("app.agents.rag.LLMFactory") as mf:
            mf.get_llm.return_value = m
            r = await rag_agent._prepare_queries("全面介绍", "broad")
        assert len(r) <= 5


# ---- _format_context ----

class TestFormatContext:
    def test_empty(self):
        from app.agents.rag import RAGAgent
        ctx, src = RAGAgent._format_context([])
        assert ctx == "" and src == []

    def test_single_result(self):
        from app.agents.rag import RAGAgent
        ctx, src = RAGAgent._format_context([_chunk(1, 0.9)])
        assert "[1]" in ctx and len(src) == 1 and src[0]["score"] == 0.9

    def test_multiple_separated(self):
        from app.agents.rag import RAGAgent
        ctx, src = RAGAgent._format_context([_chunk(i) for i in range(3)])
        assert all(f"[{i}]" in ctx for i in range(1, 4))
        assert "---" in ctx and len(src) == 3

    def test_long_content_truncated(self):
        from app.agents.rag import RAGAgent
        c = _chunk(1); c["content"] = "A" * 3000
        ctx, _ = RAGAgent._format_context([c])
        assert "..." in ctx

    def test_source_fields(self):
        from app.agents.rag import RAGAgent
        _, src = RAGAgent._format_context([_chunk(1, 0.75, "m.docx")])
        assert src[0]["source"] == "m.docx" and "snippet" in src[0]


# ---- answer() end-to-end ----

class TestAnswer:
    @pytest.mark.asyncio
    async def test_high_confidence(self, rag_agent):
        rag_agent.rag.search.return_value = {"results": [_chunk(1, 0.85)]}
        m = AsyncMock(); m.ainvoke.return_value = _llm_resp("高置信度回答 [1]")
        with patch("app.agents.rag.LLMFactory") as mf, \
             patch("app.agents.rag.query_analyzer") as qa, \
             patch("app.agents.rag.get_self_rag"), \
             patch("app.agents.rag.settings") as ms:
            mf.get_llm.return_value = m
            qa.analyze = AsyncMock(return_value=_mock_analysis())
            qa.get_strategy_config.return_value = {"top_k": 8}
            ms.RERANK_TOP_K, ms.CONFIDENCE_THRESHOLD = 8, 0.6
            ms.LLM_RELEVANCE_ENABLED, ms.CRAG_ENABLED = False, False
            r = await rag_agent.answer("Milvus怎么创建索引")
        # confidence 经 sigmoid 归一化: sigmoid(0.85) ≈ 0.7006
        assert abs(r["confidence"] - 0.7006) < 0.001 and r["context_used"] == 1
        assert r["query_type"] == "specific" and "高置信度回答" in r["answer"]

    @pytest.mark.asyncio
    async def test_low_confidence(self, rag_agent):
        rag_agent.rag.search.return_value = {"results": [_chunk(1, 0.3)]}
        m = AsyncMock(); m.ainvoke.return_value = _llm_resp("低置信度回答")
        with patch("app.agents.rag.LLMFactory") as mf, \
             patch("app.agents.rag.query_analyzer") as qa, \
             patch("app.agents.rag.get_self_rag"), \
             patch("app.agents.rag.settings") as ms:
            mf.get_llm.return_value = m
            qa.analyze = AsyncMock(return_value=_mock_analysis())
            qa.get_strategy_config.return_value = {"top_k": 8}
            ms.RERANK_TOP_K, ms.CONFIDENCE_THRESHOLD = 8, 0.6
            ms.LLM_RELEVANCE_ENABLED, ms.CRAG_ENABLED = False, False
            r = await rag_agent.answer("模糊问题")
        # confidence 经 sigmoid 归一化: sigmoid(0.3) ≈ 0.5744
        assert abs(r["confidence"] - 0.5744) < 0.001 and r["confidence"] < 0.6

    @pytest.mark.asyncio
    async def test_empty_results_early_return(self, rag_agent):
        rag_agent.rag.search.return_value = {"results": []}
        with patch("app.agents.rag.LLMFactory") as mf, \
             patch("app.agents.rag.query_analyzer") as qa, \
             patch("app.agents.rag.settings") as ms:
            qa.analyze = AsyncMock(return_value=_mock_analysis())
            qa.get_strategy_config.return_value = {"top_k": 8}
            ms.RERANK_TOP_K, ms.CRAG_ENABLED = 8, False
            r = await rag_agent.answer("不存在的问题")
        # When results are empty, should return early with 0 confidence
        assert r["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_deduplication(self, rag_agent):
        dup = _chunk(1, 0.9)
        rag_agent.rag.search.return_value = {"results": [dup, dup, dup]}
        m = AsyncMock(); m.ainvoke.return_value = _llm_resp("回答")
        with patch("app.agents.rag.LLMFactory") as mf, \
             patch("app.agents.rag.query_analyzer") as qa, \
             patch("app.agents.rag.get_self_rag"), \
             patch("app.agents.rag.settings") as ms:
            mf.get_llm.return_value = m
            qa.analyze = AsyncMock(return_value=_mock_analysis())
            qa.get_strategy_config.return_value = {"top_k": 8}
            ms.RERANK_TOP_K, ms.CONFIDENCE_THRESHOLD = 8, 0.6
            ms.LLM_RELEVANCE_ENABLED, ms.CRAG_ENABLED = False, False
            r = await rag_agent.answer("去重测试")
        assert r["context_used"] == 1
