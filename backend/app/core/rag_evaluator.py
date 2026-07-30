"""
向后兼容 re-export — 已迁移到 app.core.retrieval_evaluator
"""
from app.core.retrieval_evaluator import (  # noqa: F401
    EvaluationResult,
    RetrievalEvaluator,
    RetrievalEvaluator as RAGEvaluator,
    retrieval_evaluator,
    retrieval_evaluator as rag_evaluator,
)
