"""
SupplyChainRAG - RAG 子包

将原 rag_engine.py 拆分为以下子模块：
- embedding  : EmbeddingEngine（文本嵌入，BGE-M3）
- reranker   : RerankerEngine（精排序，BGE-Reranker）
- bm25       : BM25Engine（关键词检索，rank_bm25）
- critic     : CriticEvaluator / QueryRewriter（CRAG 评估与查询改写）
- engine     : RAGEngine（主控引擎）+ rag_engine 全局单例
"""
from app.core.rag.embedding import EmbeddingEngine
from app.core.rag.reranker import RerankerEngine
from app.core.rag.bm25 import BM25Engine
from app.core.rag.critic import CriticEvaluator, QueryRewriter
from app.core.rag.engine import RAGEngine, rag_engine

__all__ = [
    "EmbeddingEngine",
    "RerankerEngine",
    "BM25Engine",
    "CriticEvaluator",
    "QueryRewriter",
    "RAGEngine",
    "rag_engine",
]
