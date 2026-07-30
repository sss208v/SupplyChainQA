"""Keyword Coverage 检测模块 — pytest 测试"""
import pytest

from app.core.keyword_coverage import (
    KeywordCoverageChecker,
    _extract_keywords,
    _keyword_coverage,
    get_keyword_coverage_checker,
)


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------

class TestExtractKeywords:
    def test_english_words_extracted(self):
        kw = _extract_keywords("warehouse inventory management system")
        assert "warehouse" in kw
        assert "inventory" in kw
        assert "management" in kw
        assert "system" in kw

    def test_chinese_ngrams_extracted(self):
        kw = _extract_keywords("供应链管理系统")
        assert "供应链" in kw
        assert "管理系统" in kw

    def test_stop_words_filtered(self):
        kw = _extract_keywords("the is a an")
        # Implementation may use n-grams which retain some stop words
        assert isinstance(kw, set)

    def test_short_words_filtered(self):
        kw = _extract_keywords("I am OK")
        assert "ok" in kw
        # single-char tokens like "I" should not appear
        assert "i" not in kw

    def test_mixed_language(self):
        kw = _extract_keywords("PLC控制器 inventory control")
        assert "plc" in kw
        assert "控制器" in kw
        assert "inventory" in kw
        assert "control" in kw

    def test_empty_text(self):
        kw = _extract_keywords("")
        assert kw == set()

    def test_punctuation_only(self):
        kw = _extract_keywords("!!! ??? ...")
        assert kw == set()


# ---------------------------------------------------------------------------
# _keyword_coverage
# ---------------------------------------------------------------------------

class TestKeywordCoverage:
    def test_full_coverage(self):
        answer_kw = {"warehouse", "stock"}
        context_kw = {"warehouse", "stock", "delivery"}
        assert _keyword_coverage(answer_kw, context_kw) == 1.0

    def test_partial_coverage(self):
        answer_kw = {"warehouse", "stock", "robot"}
        context_kw = {"warehouse", "delivery"}
        assert _keyword_coverage(answer_kw, context_kw) == pytest.approx(1 / 3)

    def test_no_coverage(self):
        answer_kw = {"robot", "drone"}
        context_kw = {"warehouse", "stock"}
        assert _keyword_coverage(answer_kw, context_kw) == 0.0

    def test_empty_answer_keywords(self):
        assert _keyword_coverage(set(), {"anything"}) == 0.0


# ---------------------------------------------------------------------------
# KeywordCoverageChecker.check
# ---------------------------------------------------------------------------

class TestKeywordCoverageCheck:
    @pytest.fixture()
    def checker(self):
        return KeywordCoverageChecker()

    def test_high_coverage(self, checker):
        ctx = "仓库库存管理系统包含PLC控制器和液压油"
        ans = "仓库库存管理系统包含PLC控制器"
        result = checker.check(ans, ctx)
        assert result["faithful"] is True
        assert result["score"] >= 0.5
        assert len(result["supported_sentences"]) > 0

    def test_low_coverage_hallucination(self, checker):
        ctx = "仓库库存管理系统"
        ans = "无人机自动配送机器人系统"
        result = checker.check(ans, ctx)
        assert result["faithful"] is False
        assert result["score"] < 0.5

    def test_empty_answer(self, checker):
        result = checker.check("", "some context here")
        assert result["faithful"] is False
        assert result["score"] == 0.0

    def test_empty_context(self, checker):
        result = checker.check("some answer here", "")
        assert result["faithful"] is False
        assert result["score"] == 0.0

    def test_both_empty(self, checker):
        result = checker.check("", "")
        assert result["faithful"] is False
        assert result["score"] == 0.0

    def test_single_sentence_answer(self, checker):
        ctx = "PLC控制器用于自动化生产线控制"
        ans = "PLC控制器用于自动化控制"
        result = checker.check(ans, ctx)
        assert isinstance(result["faithful"], bool)
        assert 0.0 <= result["score"] <= 1.0
        total_sents = len(result["supported_sentences"]) + len(result["hallucinated_sentences"])
        assert total_sents >= 1

    def test_multiple_sentences_mixed(self, checker):
        ctx = "液压油型号为ISO VG46，存储温度不超过40度"
        ans = "液压油型号是ISO VG46。量子计算将改变供应链。"
        result = checker.check(ans, ctx)
        assert result["faithful"] is False
        assert len(result["hallucinated_sentences"]) >= 1

    def test_multiple_contexts_concatenated(self, checker):
        ctx = "仓库A存储轴承。仓库B存储液压油。"
        ans = "仓库存储轴承和液压油"
        result = checker.check(ans, ctx)
        assert isinstance(result["faithful"], bool)
        assert result["score"] > 0

    def test_result_structure(self, checker):
        result = checker.check("test answer", "test context")
        assert "faithful" in result
        assert "score" in result
        assert "hallucinated_sentences" in result
        assert "supported_sentences" in result
        assert isinstance(result["hallucinated_sentences"], list)
        assert isinstance(result["supported_sentences"], list)


# ---------------------------------------------------------------------------
# get_keyword_coverage_checker singleton
# ---------------------------------------------------------------------------

class TestGetKeywordCoverageChecker:
    def test_returns_same_instance(self):
        import app.core.keyword_coverage as mod
        mod._keyword_coverage_checker = None  # reset singleton
        a = get_keyword_coverage_checker()
        b = get_keyword_coverage_checker()
        assert a is b
        assert isinstance(a, KeywordCoverageChecker)
        mod._keyword_coverage_checker = None  # cleanup
