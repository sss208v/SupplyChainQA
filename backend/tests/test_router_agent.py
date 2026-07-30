"""
RouterAgent (意图路由) 单元测试 — 纯 mock，无外部依赖

覆盖范围：
  1. IntentType 枚举完整性
  2. _rule_match: greeting / 实体优先 / tool_call / graph_query / goal / rag问句 / None
  3. route(): 规则命中直接返回 / 规则未命中走 LLM
  4. _llm_classify: 正常 JSON / 异常兜底 / 幻觉工具名校验
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.router import RouterAgent, IntentType, GRAPH_KEYWORDS


# ---------------------------------------------------------------------------
# 1. TestIntentType
# ---------------------------------------------------------------------------

class TestIntentType:
    """验证 IntentType 枚举包含所有预期值。"""

    def test_all_intent_types_defined(self):
        expected = {"greeting", "rag_answer", "tool_call", "graph_query", "goal", "hybrid", "unclear"}
        actual = {e.value for e in IntentType}
        assert expected == actual

    def test_enum_is_string(self):
        assert IntentType.GREETING == "greeting"
        assert isinstance(IntentType.RAG_ANSWER, str)


# ---------------------------------------------------------------------------
# 2. TestRuleMatch
# ---------------------------------------------------------------------------

class TestRuleMatch:
    """_rule_match: 基于关键词/正则的快速路由。"""

    def setup_method(self):
        self.router = RouterAgent()

    def test_short_greeting_returns_greeting_intent(self):
        result = self.router._rule_match("你好")
        assert result is not None
        assert result["intent"] == IntentType.GREETING
        assert result["method"] == "rule"

    def test_long_greeting_with_business_does_not_match(self):
        """长问候 + 业务词 → 不匹配 greeting（继续往下走）"""
        result = self.router._rule_match("你好，帮我查库存MAT-001有多少")
        # 应该命中 tool_call (查库存) 而不是 greeting
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL

    def test_tool_keyword_query_inventory(self):
        result = self.router._rule_match("帮我查库存")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "query_inventory"

    def test_tool_keyword_query_order(self):
        result = self.router._rule_match("查订单PO-001")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "query_order"

    def test_tool_keyword_create_ticket(self):
        result = self.router._rule_match("建工单")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "create_ticket"

    def test_tool_keyword_get_datetime(self):
        result = self.router._rule_match("今天几号")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "get_datetime"

    def test_tool_keyword_query_supplier(self):
        result = self.router._rule_match("查供应商SUP-001信息")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "query_supplier"

    def test_graph_query_with_entity_code_and_keyword(self):
        """含实体编码 + 图谱关键词 → graph_query"""
        result = self.router._rule_match("MAT-001 缺货影响哪些物料")
        assert result is not None
        assert result["intent"] == IntentType.GRAPH_QUERY
        assert result["method"] == "rule"

    def test_graph_query_po_entity(self):
        result = self.router._rule_match("PO-123 延迟影响的订单")
        assert result is not None
        assert result["intent"] == IntentType.GRAPH_QUERY

    def test_goal_keywords(self):
        result = self.router._rule_match("帮我评估当前库存风险")
        assert result is not None
        assert result["intent"] == IntentType.GOAL
        assert result["method"] == "rule"

    def test_goal_keywords_analysis(self):
        result = self.router._rule_match("帮我分析供应商延迟问题")
        assert result is not None
        assert result["intent"] == IntentType.GOAL

    def test_rag_keywords_concept(self):
        result = self.router._rule_match("什么是安全库存")
        assert result is not None
        assert result["intent"] == IntentType.RAG_ANSWER
        assert result["method"] == "rule"

    def test_rag_keywords_flow(self):
        result = self.router._rule_match("IQC来料检验流程是什么")
        assert result is not None
        assert result["intent"] == IntentType.RAG_ANSWER

    def test_rag_keywords_comparison(self):
        result = self.router._rule_match("MRP和ERP有什么区别")
        assert result is not None
        assert result["intent"] == IntentType.RAG_ANSWER

    def test_no_match_returns_none(self):
        """纯随机文本 → 无规则命中，返回 None"""
        result = self.router._rule_match("xyz")
        assert result is None

    def test_generic_domain_word_sinks_to_semantic(self):
        """领域泛词不再规则短路 → 下沉语义层（返回 None）

        旧版泛词大表会把含"库存""采购"的任意 query 短路为 RAG，
        现在这类无明确问句形态的 query 交给语义路由判断。
        """
        result = self.router._rule_match("介绍一下供应链采购中心的情况")
        assert result is None

    def test_tool_keywords_take_priority_over_rag(self):
        """工具命令词优先于 RAG 问句正则"""
        # "查库存" 是精确命令词
        result = self.router._rule_match("查库存情况")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL


# ---------------------------------------------------------------------------
# 2.5 TestEntityFirstRouting — 实体编码优先路由（修复泛词误判）
# ---------------------------------------------------------------------------

class TestEntityFirstRouting:
    """含 MAT-/PO-/TK- 编码的查询：实体规则优先于其他匹配。"""

    def setup_method(self):
        self.router = RouterAgent()

    def test_entity_with_inventory_hint_routes_to_tool(self):
        """旧版被泛词"库存"误判为 RAG 的经典 case → 现在路由到工具"""
        result = self.router._rule_match("MAT-001 还剩多少库存")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "query_inventory"

    def test_entity_po_with_order_hint_routes_to_tool(self):
        result = self.router._rule_match("PO-2025030 的到货状态怎么样")
        assert result is not None
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "query_order"

    def test_entity_with_knowledge_question_stays_rag(self):
        """仅含编码不强制 tool_call：知识问句仍走 RAG"""
        result = self.router._rule_match("MAT-001 的质检验收标准是什么")
        assert result is not None
        assert result["intent"] == IntentType.RAG_ANSWER

    def test_entity_graph_keyword_beats_tool_hint(self):
        """编码 + 关系词同时含库存提示词 → 优先 graph_query"""
        result = self.router._rule_match("MAT-001 缺货影响哪些物料的库存")
        assert result is not None
        assert result["intent"] == IntentType.GRAPH_QUERY


# ---------------------------------------------------------------------------
# 3. TestRoute
# ---------------------------------------------------------------------------

class TestRoute:
    """route(): 三层路由策略（规则 → 语义 → LLM）。"""

    def setup_method(self):
        self.router = RouterAgent()

    async def test_rule_match_short_circuits_to_greeting(self):
        """规则命中 → 直接返回，不走语义和 LLM。"""
        result = await self.router.route("你好")
        assert result["intent"] == IntentType.GREETING
        assert result["method"] == "rule"

    async def test_rule_match_tool_call(self):
        result = await self.router.route("查库存MAT-001")
        assert result["intent"] == IntentType.TOOL_CALL
        assert result["tool_name"] == "query_inventory"
        assert result["method"] == "rule"

    @patch("app.agents.router._semantic_ready", False)
    @patch("app.agents.router._ensure_semantic_router")
    async def test_llm_fallback_when_no_rule_no_semantic(self, mock_ensure):
        """规则未命中 + 语义未就绪 → 走 LLM 分类。"""
        with patch.object(self.router, "_llm_classify", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": IntentType.RAG_ANSWER,
                "confidence": 0.8,
                "tool_name": None,
                "method": "llm",
            }
            result = await self.router.route("随机不明确的查询xyz")
            assert result["intent"] == IntentType.RAG_ANSWER
            assert result["method"] == "llm"
            mock_llm.assert_called_once()


# ---------------------------------------------------------------------------
# 4. TestLLMClassify
# ---------------------------------------------------------------------------

class TestLLMClassify:
    """_llm_classify: LLM 意图分类的正常/异常/校验行为。"""

    def setup_method(self):
        self.router = RouterAgent()

    @patch("app.agents.router.LLMFactory")
    async def test_valid_json_response_parsed_correctly(self, mock_factory):
        """LLM 返回合法 JSON → 正确解析 intent + confidence。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"intent": "rag_answer", "confidence": 0.9, "tool_name": null}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_factory.get_llm.return_value = mock_llm

        result = await self.router._llm_classify("什么是VMI模式")
        assert result["intent"] == IntentType.RAG_ANSWER
        assert result["confidence"] == 0.9
        assert result["tool_name"] is None
        assert result["method"] == "llm"

    @patch("app.agents.router.LLMFactory")
    async def test_llm_exception_returns_unclear(self, mock_factory):
        """LLM 调用异常 → 兜底返回 unclear。"""
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        mock_factory.get_llm.return_value = mock_llm

        result = await self.router._llm_classify("test query")
        assert result["intent"] == IntentType.UNCLEAR
        assert result["confidence"] == 0.3
        assert result["method"] == "llm"

    @patch("app.agents.router.TOOL_REGISTRY", {"query_inventory": MagicMock()})
    @patch("app.agents.router.LLMFactory")
    async def test_hallucinated_tool_name_rejected(self, mock_factory):
        """LLM 返回不存在的工具名 → validated_tool 为 None。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"intent": "tool_call", "confidence": 0.7, "tool_name": "fake_tool"}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_factory.get_llm.return_value = mock_llm

        result = await self.router._llm_classify("用某个工具")
        assert result["intent"] == IntentType.TOOL_CALL
        # fake_tool 不在 TOOL_REGISTRY → 被过滤
        assert result["tool_name"] is None

    @patch("app.agents.router.TOOL_REGISTRY", {"query_inventory": MagicMock()})
    @patch("app.agents.router.LLMFactory")
    async def test_valid_tool_name_preserved(self, mock_factory):
        """LLM 返回合法工具名 → 保留。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"intent": "tool_call", "confidence": 0.85, "tool_name": "query_inventory"}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_factory.get_llm.return_value = mock_llm

        result = await self.router._llm_classify("查一下MAT-001的库存")
        assert result["tool_name"] == "query_inventory"

    @patch("app.agents.router.LLMFactory")
    async def test_invalid_intent_string_falls_to_unclear(self, mock_factory):
        """LLM 返回非法 intent 字符串 → 降级为 unclear。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"intent": "nonexistent_intent", "confidence": 0.5, "tool_name": null}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_factory.get_llm.return_value = mock_llm

        result = await self.router._llm_classify("xyz")
        assert result["intent"] == IntentType.UNCLEAR
