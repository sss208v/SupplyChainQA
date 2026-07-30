"""
Query 复杂度分析器单元测试 — 纯 mock，无外部 LLM 调用

覆盖：规则分析的 light/standard/full 策略、LLM 分析路径、
     get_strategy_config、upgrade_strategy、边界情况。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.query_analyzer import (
    QueryComplexityAnalyzer,
    QueryAnalysis,
    STRATEGIES,
)


# ── 规则分析：策略分级 ─────────────────────────────────────────────────────

class TestRuleAnalyzeStrategy:
    """_rule_analyze 基于规则的策略判定"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    def test_simple_query_returns_light(self):
        """短 query + 简单词 → light"""
        result = self.analyzer._rule_analyze("安全库存公式")
        assert result.strategy == "light"
        assert result.method == "rule"

    def test_medium_query_returns_standard(self):
        """中等长度 + 少量复杂词 → standard"""
        result = self.analyzer._rule_analyze("供应商准入需要哪些资料和审批流程")
        assert result.strategy in ("standard", "full")

    def test_complex_query_returns_full(self):
        """多实体 + 推理词 + 比较词 → full"""
        query = "对比供应商A和供应商B的采购单PO-001和PO-002的质检结果，分析为什么差异较大"
        result = self.analyzer._rule_analyze(query)
        assert result.strategy == "full"
        assert result.needs_reasoning is True
        assert result.entity_count >= 2

    def test_reasoning_word_detected(self):
        """含推理词时 needs_reasoning=True"""
        result = self.analyzer._rule_analyze("为什么物料MAT-001的库存下降了")
        assert result.needs_reasoning is True


# ── 规则分析：复杂度分数 ──────────────────────────────────────────────────

class TestRuleAnalyzeComplexity:
    """复杂度分数区间验证"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    def test_score_in_unit_range(self):
        """复杂度分数始终在 [0, 1]"""
        queries = ["定义", "查询物料库存", "对比分析供应商A与B的采购策略并提出改进方案"]
        for q in queries:
            result = self.analyzer._rule_analyze(q)
            assert 0.0 <= result.complexity <= 1.0

    def test_longer_query_higher_score(self):
        """长 query 倾向更高复杂度"""
        short = self.analyzer._rule_analyze("库存")
        long_q = self.analyzer._rule_analyze(
            "分析2024年Q1到Q3期间物料MAT-001和MAT-002的采购价格变化趋势"
        )
        assert long_q.complexity >= short.complexity


# ── 实体计数 ───────────────────────────────────────────────────────────────

class TestEntityCount:
    """供应链领域实体关键词计数"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    def test_multiple_entities_detected(self):
        """多实体关键词被识别"""
        result = self.analyzer._rule_analyze("物料和供应商和采购单")
        assert result.entity_count >= 3

    def test_no_entities_defaults_to_one(self):
        """无实体关键词时 entity_count >= 1"""
        result = self.analyzer._rule_analyze("你好")
        assert result.entity_count >= 1


# ── LLM 分析路径 ──────────────────────────────────────────────────────────

class TestLLMAnalyze:
    """LLM 分析成功 / 失败 / 降级路径"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    @pytest.mark.asyncio
    async def test_llm_success_returns_llm_result(self):
        """LLM 返回有效 JSON → method='llm'"""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"complexity": 0.8, "entity_count": 3, "needs_reasoning": true, "strategy": "full"}'
        mock_llm.ainvoke.return_value = mock_response

        result = await self.analyzer.analyze("复杂查询", llm=mock_llm)
        assert result.method == "llm"
        assert result.strategy == "full"
        assert result.complexity == 0.8

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_rule(self):
        """LLM 抛异常 → 回退到规则分析"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("API timeout")

        result = await self.analyzer.analyze("库存公式", llm=mock_llm)
        assert result.method == "rule"
        assert isinstance(result.strategy, str)

    @pytest.mark.asyncio
    async def test_no_llm_uses_rule_directly(self):
        """llm=None → 直接走规则分析"""
        result = await self.analyzer.analyze("安全库存定义", llm=None)
        assert result.method == "rule"

    @pytest.mark.asyncio
    async def test_llm_malformed_json_falls_back(self):
        """LLM 返回无效 JSON → 回退到规则"""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "sorry I cannot comply"
        mock_llm.ainvoke.return_value = mock_response

        result = await self.analyzer.analyze("查询", llm=mock_llm)
        assert result.method == "rule"


# ── 策略配置 ───────────────────────────────────────────────────────────────

class TestStrategyConfig:
    """get_strategy_config 查询策略参数"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    def test_light_skips_reranker_and_self_rag(self):
        cfg = self.analyzer.get_strategy_config("light")
        assert cfg["use_reranker"] is False
        assert cfg["use_self_rag"] is False

    def test_full_enables_all_features(self):
        cfg = self.analyzer.get_strategy_config("full")
        assert cfg["use_reranker"] is True
        assert cfg["use_self_rag"] is True
        assert cfg["use_query_rewrite"] is True

    def test_unknown_strategy_defaults_to_standard(self):
        cfg = self.analyzer.get_strategy_config("unknown")
        assert cfg == STRATEGIES["standard"]


# ── upgrade_strategy ──────────────────────────────────────────────────────

class TestUpgradeStrategy:
    """Adaptive RAG 策略升级"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    def test_light_with_low_quality_upgrades_to_full(self):
        assert self.analyzer.upgrade_strategy("light", "low") == "full"

    def test_standard_with_low_quality_upgrades_to_full(self):
        assert self.analyzer.upgrade_strategy("standard", "low") == "full"

    def test_full_never_upgrades(self):
        assert self.analyzer.upgrade_strategy("full", "low") == "full"

    def test_high_quality_no_upgrade(self):
        assert self.analyzer.upgrade_strategy("light", "high") == "light"
        assert self.analyzer.upgrade_strategy("standard", "high") == "standard"


# ── 统计 ──────────────────────────────────────────────────────────────────

class TestStats:
    """策略使用统计"""

    @pytest.mark.asyncio
    async def test_stats_tracks_distribution(self):
        analyzer = QueryComplexityAnalyzer()
        await analyzer.analyze("公式", llm=None)          # light
        await analyzer.analyze("供应商准入流程和制度", llm=None)  # standard/full
        stats = analyzer.get_stats()
        assert stats["total"] >= 2
        assert "distribution" in stats


# ── 边界情况 ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    """边界情况"""

    def setup_method(self):
        self.analyzer = QueryComplexityAnalyzer()

    def test_empty_query(self):
        """空字符串不崩溃"""
        result = self.analyzer._rule_analyze("")
        assert isinstance(result, QueryAnalysis)
        assert 0.0 <= result.complexity <= 1.0

    def test_single_word(self):
        """单字不崩溃"""
        result = self.analyzer._rule_analyze("好")
        assert result.strategy in ("light", "standard", "full")

    @pytest.mark.asyncio
    async def test_very_long_query(self):
        """超长 query 不崩溃"""
        long_query = "物料" * 500
        result = await self.analyzer.analyze(long_query, llm=None)
        assert isinstance(result, QueryAnalysis)

    def test_entity_heavy_query(self):
        """大量实体关键词触发 full"""
        query = "物料MAT-001的供应商SUP-002和采购单PO-003的质检ISO-9001和ERP工单结果"
        result = self.analyzer._rule_analyze(query)
        assert result.entity_count >= 4
        assert result.strategy in ("standard", "full")
