"""
SupplyChainRAG - 语义路由模块

语义路由用 embedding 相似度替代 LLM 分类：
- 预计算每个路由类型的代表 query 的 embedding
- 用户 query 来了直接算余弦相似度
- <10ms，零 token 成本

对比 LLM 分类（2.5秒 + token 成本），语义路由快 100 倍。

规则匹配处理明确的关键词场景，语义路由用 embedding 相似度处理模糊场景。
只有两者都失败时才回退到 LLM 分类。这样 90% 的请求不需要调 LLM，节省 token。

【v2 增强】
- 路由样本从 app/data/intent_routes.json 读取（配置化，支持 goal/graph_query）
- 阈值可配置：全局 SEMANTIC_ROUTER_THRESHOLD + per-intent threshold 覆盖
- top1-top2 margin 判据：意图间分差过小视为模糊，回退 LLM（降低近邻误判）
- reload()：配置更新后重建路由 embedding（配合热加载端点）
"""
import logging
import numpy as np
from typing import Optional
from dataclasses import dataclass

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class SemanticRoute:
    """语义路由结果"""
    intent: str         # rag_answer / tool_call / greeting / goal / graph_query
    confidence: float   # 相似度分数
    method: str = "semantic"


class SemanticRouter:
    """语义路由器

    预计算每个路由类型的代表 query embedding，
    用户 query 来了直接算余弦相似度，选最近的路由。

    样本来源优先级：intent_routes.json 配置 > 内置 ROUTE_EXAMPLES（兜底）。
    """

    # 内置兜底样本（配置文件缺失/为空时使用；正常以 intent_routes.json 为准）
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

    # 兼容旧引用的默认阈值（运行时以 settings.SEMANTIC_ROUTER_THRESHOLD 为准）
    SIMILARITY_THRESHOLD = 0.65

    def __init__(self):
        self._route_embeddings: dict[str, list[np.ndarray]] = {}
        self._thresholds: dict[str, float] = {}  # per-intent 阈值覆盖
        self._initialized = False
        self._embedding_engine = None
        self._config_version: int = -1

    def _load_examples(self) -> tuple[dict[str, list[str]], dict[str, float], int]:
        """加载语义样本：优先 intent_routes.json，为空时回退内置 ROUTE_EXAMPLES"""
        try:
            from app.core.intent_routes import get_intent_routes
            cfg = get_intent_routes()
            if cfg.semantic_routes:
                examples = {
                    intent: route["utterances"]
                    for intent, route in cfg.semantic_routes.items()
                }
                thresholds = {
                    intent: float(route["threshold"])
                    for intent, route in cfg.semantic_routes.items()
                    if route.get("threshold") is not None
                }
                return examples, thresholds, cfg.version
        except Exception as e:
            logger.warning(f"[SemanticRouter] 配置加载失败，使用内置样本: {e}")
        return dict(self.ROUTE_EXAMPLES), {}, -1

    def init(self, embedding_engine):
        """预计算路由 embedding（启动时执行一次）"""
        if self._initialized:
            return
        self._embedding_engine = embedding_engine
        self._build()

    def reload(self, embedding_engine=None) -> bool:
        """重建路由 embedding（intent_routes.json 更新后调用）

        Returns:
            True 重建成功，False 失败（embedding 引擎未就绪等）
        """
        if embedding_engine is not None:
            self._embedding_engine = embedding_engine
        if self._embedding_engine is None:
            logger.warning("[SemanticRouter] embedding 引擎未就绪，无法重建路由")
            return False
        self._initialized = False
        self._build()
        return self._initialized

    def _build(self):
        """从样本构建路由 embedding（成功后整体替换，失败保留旧状态）"""
        try:
            examples, thresholds, version = self._load_examples()
            route_embeddings: dict[str, list[np.ndarray]] = {}
            for intent, texts in examples.items():
                embeddings = []
                for text in texts:
                    vec = self._embedding_engine.embed_query(text)
                    embeddings.append(np.array(vec))
                route_embeddings[intent] = embeddings

            self._route_embeddings = route_embeddings
            self._thresholds = thresholds
            self._config_version = version
            self._initialized = True
            total = sum(len(v) for v in self._route_embeddings.values())
            logger.info(
                f"[SemanticRouter] 初始化完成: {total} 个路由样本 / "
                f"{len(route_embeddings)} 个意图 (config v{version})"
            )
        except Exception as e:
            logger.warning(f"[SemanticRouter] 初始化失败: {e}")

    def route(self, query_embedding: list[float]) -> Optional[SemanticRoute]:
        """根据 query embedding 语义路由

        判据（两道门槛，宁可多走 LLM 也不要误路由）：
        1. 阈值：top1 意图分数 >= per-intent 阈值（缺省用全局 SEMANTIC_ROUTER_THRESHOLD）
        2. margin：top1-top2 意图分差 >= SEMANTIC_ROUTER_MARGIN（分差过小视为模糊）

        Args:
            query_embedding: 用户 query 的 embedding 向量

        Returns:
            SemanticRoute 如果匹配成功，None 如果需要回退到 LLM
        """
        if not self._initialized:
            return None

        query_vec = np.array(query_embedding)

        # 每个意图取与其所有样本相似度的最大值
        intent_scores: dict[str, float] = {}
        for intent, embeddings in self._route_embeddings.items():
            best = -1.0
            for vec in embeddings:
                score = self._cosine_similarity(query_vec, vec)
                if score > best:
                    best = score
            intent_scores[intent] = best

        if not intent_scores:
            return None

        ranked = sorted(intent_scores.items(), key=lambda kv: kv[1], reverse=True)
        best_intent, best_score = ranked[0]

        # 门槛1：阈值（per-intent 覆盖 > 全局配置）
        threshold = self._thresholds.get(best_intent, settings.SEMANTIC_ROUTER_THRESHOLD)
        if best_score < threshold:
            logger.info(
                f"[SemanticRouter] 最高分 {best_score:.3f} < {threshold}，回退LLM"
            )
            return None

        # 门槛2：margin（多意图时 top1-top2 分差过小 → 模糊，回退 LLM）
        margin = None
        if len(ranked) > 1:
            margin = best_score - ranked[1][1]
            if margin < settings.SEMANTIC_ROUTER_MARGIN:
                logger.info(
                    f"[SemanticRouter] margin={margin:.3f} < "
                    f"{settings.SEMANTIC_ROUTER_MARGIN}（{best_intent} vs {ranked[1][0]}），"
                    f"意图模糊回退LLM"
                )
                return None

        # 结构化决策日志（供 tune_router_thresholds.py 离线调参消费）
        logger.info(
            f"[SemanticRouter] intent={best_intent} score={best_score:.3f} "
            f"margin={f'{margin:.3f}' if margin is not None else 'n/a'}"
        )
        return SemanticRoute(
            intent=best_intent,
            confidence=best_score,
        )

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
