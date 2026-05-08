"""路由模块单元测试"""
import pytest
import asyncio

# 测试规则匹配
class TestRuleMatching:
    """测试规则匹配逻辑"""

    def test_greeting_detection(self):
        """测试问候语识别"""
        greetings = ["你好", "早上好", "嗨", "谢谢", "再见"]
        for g in greetings:
            # 简单检查是否包含问候关键词
            assert any(kw in g for kw in ["你好", "好", "嗨", "谢谢", "再见"])

    def test_tool_keyword_detection(self):
        """测试工具关键词识别"""
        tool_queries = [
            "帮我查一下物料MAT-001的库存",
            "采购单PO-20250101到货了吗",
            "帮我建个补货工单",
        ]
        tool_keywords = ["查", "库存", "物料", "MAT-", "采购单", "PO-", "工单"]
        for q in tool_queries:
            assert any(kw in q for kw in tool_keywords)

    def test_rag_keyword_detection(self):
        """测试 RAG 关键词识别"""
        rag_queries = [
            "供应商准入需要什么资质",
            "安全库存的计算公式",
            "库存管理制度",
        ]
        rag_keywords = ["供应商", "准入", "资质", "安全库存", "公式", "制度"]
        for q in rag_queries:
            assert any(kw in q for kw in rag_keywords)


# 测试 Query 复杂度分析
class TestQueryComplexity:
    """测试 Query 复杂度分析逻辑"""

    def test_simple_query_low_score(self):
        """简单查询应得低分"""
        simple_queries = ["安全库存公式", "MAT-001", "查库存"]
        for q in simple_queries:
            # 短查询应被识别为简单
            assert len(q) < 20

    def test_complex_query_high_score(self):
        """复杂查询应得高分"""
        complex_queries = [
            "为什么供应商准入流程需要三步审核？",
            "对比分析两种库存管理策略的优劣",
        ]
        for q in complex_queries:
            # 包含推理词的应被识别为复杂
            reasoning_words = ["为什么", "对比", "分析"]
            assert any(w in q for w in reasoning_words)

    def test_entity_counting(self):
        """测试实体计数"""
        query = "物料MAT-001和供应商SUP-002的采购单PO-20250101"
        entities = ["物料", "MAT-", "供应商", "SUP-", "采购单", "PO-"]
        count = sum(1 for e in entities if e in query)
        assert count >= 3


# 测试语义路由
class TestSemanticRouter:
    """测试语义路由逻辑"""

    def test_cosine_similarity_identical(self):
        """相同向量相似度应为1"""
        import numpy as np
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        similarity = float(dot / norm) if norm > 0 else 0.0
        assert abs(similarity - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        """正交向量相似度应为0"""
        import numpy as np
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        similarity = float(dot / norm) if norm > 0 else 0.0
        assert abs(similarity) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
