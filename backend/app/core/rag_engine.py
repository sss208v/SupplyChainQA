"""
向后兼容 re-export — 已拆分到 app.core.rag 子包

真实实现（修改请落在这些文件，本文件只做转发）：
- RAGEngine / rag_engine          -> app/core/rag/engine.py
- EmbeddingEngine                 -> app/core/rag/embedding.py
- RerankerEngine                  -> app/core/rag/reranker.py
- BM25Engine                      -> app/core/rag/bm25.py
- CriticEvaluator / QueryRewriter -> app/core/rag/critic.py
"""
from app.core.rag.embedding import EmbeddingEngine  # noqa: F401
from app.core.rag.reranker import RerankerEngine  # noqa: F401
from app.core.rag.bm25 import BM25Engine  # noqa: F401
from app.core.rag.critic import CriticEvaluator, QueryRewriter  # noqa: F401
from app.core.rag.engine import RAGEngine, rag_engine  # noqa: F401
