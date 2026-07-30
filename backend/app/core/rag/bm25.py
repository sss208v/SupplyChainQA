"""
SupplyChainRAG - BM25 关键词检索引擎

提供 BM25Engine 类，使用 rank_bm25 库实现标准 BM25 算法：
- IDF 逆文档频率
- 文档长度归一化
- 饱和函数（term frequency saturation）

支持中英文混合分词（jieba + 正则），并提供文档索引、检索和删除功能。
"""
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BM25Engine:
    """
    BM25关键词检索引擎

    使用 rank_bm25 库实现真正的 BM25 算法：
    - IDF 逆文档频率
    - 文档长度归一化
    - 饱和函数（term frequency saturation）
    """

    def __init__(self):
        self._tokenized_corpus: list[list[str]] = []
        self._chunks: list[dict] = []  # 保留原始 chunk 信息用于返回
        self._bm25: Optional[Any] = None  # BM25Okapi (延迟导入)
        self._doc_index_map: dict[str, int] = {}  # chunk_id -> corpus index

    def index_documents(self, doc_id: str, chunks: list[dict], security_group: list[str] | None = None):
        """
        索引文档切片

        Args:
            doc_id: 文档ID
            chunks: 切片列表 [{chunk_id, content, source, page_num}]
        """
        security_group = security_group or ["admin"]

        # 清理旧数据（如果已存在）
        self._remove_doc_by_id(doc_id)

        # 分词并构建语料库
        start_idx = len(self._tokenized_corpus)
        for i, chunk in enumerate(chunks):
            tokens = self._tokenize(chunk["content"])
            self._tokenized_corpus.append(tokens)
            # 记录 chunk_id -> corpus index 的映射
            chunk_id = chunk.get("chunk_id", f"{doc_id}_{i}")
            self._doc_index_map[chunk_id] = start_idx + i
            # 保存原始 chunk 信息
            self._chunks.append({
                **chunk,
                "chunk_id": chunk_id,
                "source": chunk.get("source", ""),
                "page_num": chunk.get("page_num", 0),
                "security_group": security_group,
            })

        # 初始化 BM25
        from rank_bm25 import BM25Okapi
        self._bm25 = BM25Okapi(self._tokenized_corpus)
        logger.info(f"BM25索引完成: doc_id={doc_id}, 切片数={len(chunks)}, 总语料={len(self._tokenized_corpus)}")

    def search(
        self,
        query: str,
        top_k: int = 20,
        allowed_roles: Optional[list[str]] = None,
        doc_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        BM25关键词检索（真正的 BM25 算法）
        """
        if not self._bm25 or not self._tokenized_corpus:
            return []

        query_tokens = self._tokenize(query)
        scores = self._bm25.get_scores(query_tokens)

        # 构造 (score, chunk) pairs
        scored = [(scores[i], self._chunks[i]) for i in range(len(self._chunks))]
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, chunk in scored:
            if doc_ids and chunk.get("doc_id") not in doc_ids:
                continue
            if allowed_roles:
                groups = set(chunk.get("security_group") or [])
                if not groups.intersection(allowed_roles):
                    continue
            results.append({
                "content": chunk["content"],
                "source": chunk.get("source", ""),
                "page_num": chunk.get("page_num", 0),
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk.get("doc_id", ""),
                "security_group": chunk.get("security_group", ["admin"]),
                "bm25_score": float(score),
                "retrieval_source": "bm25",
            })
            if len(results) >= top_k:
                break

        return results

    def _remove_doc_by_id(self, doc_id: str):
        """删除指定 doc_id 的文档索引"""
        # 找出所有属于该 doc_id 的 chunk 索引
        indices_to_remove = []
        new_chunks = []
        new_tokenized = []
        new_index_map = {}

        for i, chunk in enumerate(self._chunks):
            if chunk.get("doc_id") == doc_id:
                indices_to_remove.append(i)
            else:
                new_idx = len(new_chunks)
                new_chunks.append(chunk)
                new_tokenized.append(self._tokenized_corpus[i])
                new_index_map[chunk["chunk_id"]] = new_idx

        if not indices_to_remove:
            return

        self._chunks = new_chunks
        self._tokenized_corpus = new_tokenized
        self._doc_index_map = new_index_map

        # 重建 BM25
        if self._tokenized_corpus:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(self._tokenized_corpus)
        else:
            self._bm25 = None

        logger.info(f"BM25索引删除: doc_id={doc_id}, 删除切片数={len(indices_to_remove)}")

    def remove_doc(self, doc_id: str):
        """删除文档索引（公开接口）"""
        self._remove_doc_by_id(doc_id)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """
        中英文混合分词

        使用 jieba 处理中文（如果可用），否则用字符级别分词。
        英文按单词拆分。
        """
        # 提取英文单词（保留大小写，因为 BM25 对大小写敏感）
        en_words = re.findall(r"[a-zA-Z]+", text)

        # 尝试使用 jieba 处理中文
        try:
            import jieba
            cn_chars = list(jieba.cut(text))
        except ImportError:
            # 回退：按字符级别分词，每2个中文字为一个token
            cn_chars = re.findall(r"[\u4e00-\u9fff]+", text)
            # 将连续中文字符串按字符拆分并重新组合为bigram
            bigrams = []
            for chars in cn_chars:
                for i in range(0, len(chars) - 1, 2):
                    bigrams.append(chars[i:i+2])
            cn_chars = bigrams

        # 提取数字
        numbers = re.findall(r"\d+", text)

        tokens = en_words + cn_chars + numbers
        return tokens
