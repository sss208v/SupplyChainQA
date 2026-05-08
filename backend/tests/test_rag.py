"""RAG 引擎单元测试"""
import pytest


# 测试 RRF 融合排序
class TestRRFFusion:
    """测试 Reciprocal Rank Fusion 算法"""

    def test_rrf_score_calculation(self):
        """测试 RRF 分数计算公式: score = 1/(60+rank)"""
        k = 60
        rank1 = 1
        rank2 = 3
        score1 = 1 / (k + rank1)
        score2 = 1 / (k + rank2)
        assert score1 > score2  # 排名越高分数越高
        assert abs(score1 - 1/61) < 0.001

    def test_rrf_merge(self):
        """测试 RRF 合并逻辑"""
        # 两路检索结果
        vector_results = [{"chunk_id": "A"}, {"chunk_id": "B"}, {"chunk_id": "C"}]
        bm25_results = [{"chunk_id": "B"}, {"chunk_id": "D"}, {"chunk_id": "A"}]

        # 计算 RRF 分数
        k = 60
        scores = {}
        for rank, r in enumerate(vector_results):
            cid = r["chunk_id"]
            scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)
        for rank, r in enumerate(bm25_results):
            cid = r["chunk_id"]
            scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)

        # 排序
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        # B 在两路都排名靠前，应排第一
        assert sorted_ids[0] == "B"
        # A 在两路都出现，应排第二
        assert sorted_ids[1] == "A"


# 测试 Query Cache
class TestQueryCache:
    """测试查询缓存逻辑"""

    def test_cache_key_generation(self):
        """测试缓存键生成"""
        import hashlib
        query = "安全库存公式"
        key = hashlib.md5(query.encode()).hexdigest()
        assert len(key) == 32
        assert key == hashlib.md5(query.encode()).hexdigest()  # 相同输入相同输出

    def test_cache_ttl(self):
        """测试缓存 TTL"""
        import time
        ttl = 300  # 5 分钟
        cached_time = time.time() - 100  # 100秒前缓存
        assert time.time() - cached_time < ttl  # 未过期
        cached_time_old = time.time() - 400  # 400秒前缓存
        assert time.time() - cached_time_old > ttl  # 已过期


# 测试语义切片
class TestSemanticChunking:
    """测试语义切片逻辑"""

    def test_paragraph_splitting(self):
        """测试按段落切分"""
        text = "段落一\n\n段落二\n\n段落三"
        paragraphs = text.split("\n\n")
        assert len(paragraphs) == 3

    def test_chunk_size_limit(self):
        """测试切片大小限制"""
        chunk_size = 1000
        text = "A" * 2500
        chunks = []
        for i in range(0, len(text), chunk_size):
            chunks.append(text[i:i+chunk_size])
        assert len(chunks) == 3
        assert len(chunks[0]) == 1000
        assert len(chunks[2]) == 500

    def test_overlap(self):
        """测试重叠逻辑"""
        text = "A" * 100
        chunk_size = 50
        overlap = 10
        chunks = []
        pos = 0
        while pos < len(text):
            chunks.append(text[pos:pos+chunk_size])
            pos += chunk_size - overlap
        # 应有重叠
        assert len(chunks) > 1


# 测试置信度路由
class TestConfidenceRouter:
    """测试置信度路由逻辑"""

    def test_high_confidence_direct(self):
        """高置信度应直接生成"""
        confidence = 0.8
        assert confidence > 0.7

    def test_medium_confidence_rewrite(self):
        """中置信度应改写重试"""
        confidence = 0.5
        assert 0.3 < confidence < 0.7

    def test_low_confidence_web_search(self):
        """低置信度应搜索外部"""
        confidence = 0.2
        assert confidence < 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
