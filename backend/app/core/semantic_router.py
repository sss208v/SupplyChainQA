"""
SmartQA - 语义路由模块

语义路由用 embedding 相似度替代 LLM 分类：
- 预计算每个路由类型的代表 query 的 embedding
- 用户 query 来了直接算余弦相似度
- <10ms，零 token 成本

对比 LLM 分类（2.5秒 + token 成本），语义路由快 100 倍。

规则匹配处理明确的关键词场景，语义路由用 embedding 相似度处理模糊场景。
只有两者都失败时才回退到 LLM 分类。这样 90% 的请求不需要调 LLM，节省 token。"
"""
import logging
import numpy as np
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SemanticRoute:
    """语义路由结果"""
    intent: str         # rag_answer / tool_call / greeting / unclear
    confidence: float   # 相似度分数
    method: str = "semantic"


class SemanticRouter:
    """语义路由器

    预计算每个路由类型的代表 query embedding，
    用户 query 来了直接算余弦相似度，选最近的路由。
    """

    # 各路由类型的代表 query（覆盖常见表达）
    ROUTE_EXAMPLES = {
        "rag_answer": [
            "供应商准入需要什么资质",
            "安全库存的计算公式是什么",
            "库存管理制度有哪些规定",
            "来料检验的标准流程",
            "采购审批的权限怎么划分",
            "物流时效是多少天",
            "供应商绩效怎么评估",
            "呆滞料怎么处理",
            "物料编码规则是什么",
            "跨部门协作流程是怎样的",
            "成本核算的方法有哪些",
            "供应链风险怎么管控",
            "什么是ABC分类法",
            "盘点差异怎么处理",
            "采购比价的规则",
        ],
        "tool_call": [
            "帮我查一下物料MAT-001的库存",
            "采购单PO-20250101到货了吗",
            "帮我建个补货工单",
            "查一下MAT-002还有多少库存",
            "MAT-003的库存够不够",
            "采购单状态查询",
            "创建一个紧急采购工单",
            "现在几点了",
            "今天日期是什么",
        ],
        "greeting": [
            "你好",
            "你是谁",
            "早上好",
            "嗨",
            "谢谢",
            "再见",
        ],
    }

    SIMILARITY_THRESHOLD = 0.55  # 低于此阈值走 LLM 分类

    def __init__(self):
        self._route_embeddings: dict[str, list[np.ndarray]] = {}
        self._initialized = False

    def init(self, embedding_engine):
        """预计算路由 embedding（启动时执行一次）"""
        if self._initialized:
            return

        try:
            for intent, examples in self.ROUTE_EXAMPLES.items():
                embeddings = []
                for text in examples:
                    vec = embedding_engine.embed_query(text)
                    embeddings.append(np.array(vec))
                self._route_embeddings[intent] = embeddings

            self._initialized = True
            total = sum(len(v) for v in self._route_embeddings.values())
            logger.info(f"[SemanticRouter] 初始化完成: {total} 个路由样本")
        except Exception as e:
            logger.warning(f"[SemanticRouter] 初始化失败: {e}")

    def route(self, query_embedding: list[float]) -> Optional[SemanticRoute]:
        """根据 query embedding 语义路由

        Args:
            query_embedding: 用户 query 的 embedding 向量

        Returns:
            SemanticRoute 如果匹配成功，None 如果需要回退到 LLM
        """
        if not self._initialized:
            return None

        query_vec = np.array(query_embedding)
        best_intent = None
        best_score = -1.0

        for intent, embeddings in self._route_embeddings.items():
            # 计算与该路由所有样本的余弦相似度，取最大值
            for vec in embeddings:
                score = self._cosine_similarity(query_vec, vec)
                if score > best_score:
                    best_score = score
                    best_intent = intent

        if best_score >= self.SIMILARITY_THRESHOLD and best_intent:
            logger.info(f"[SemanticRouter] intent={best_intent} score={best_score:.3f}")
            return SemanticRoute(
                intent=best_intent,
                confidence=best_score,
            )

        logger.info(f"[SemanticRouter] 最高分 {best_score:.3f} < {self.SIMILARITY_THRESHOLD}，回退LLM")
        return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """余弦相似度"""
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return 0.0
        return float(dot / norm)


# 单例
_semantic_router: Optional[SemanticRouter] = None

def get_semantic_router() -> SemanticRouter:
    global _semantic_router
    if _semantic_router is None:
        _semantic_router = SemanticRouter()
    return _semantic_router
