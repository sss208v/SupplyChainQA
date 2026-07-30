"""
SupplyChainRAG - 意图路由Agent
============================================================
1. 意图路由是Multi-Agent系统的"大脑"，负责判断用户问题该交给哪个Agent处理
2. 本实现采用三层策略（hybrid routing，与业界 semantic-router 思路一致）：
   - 规则层：只做确定性短路（实体编码/命令词/问句形态），零延迟零误判
   - 语义层：embedding 相似度，覆盖率主力（<10ms，零 token）
   - LLM层：LLM 意图分类作为最后一层回退，适合模糊意图
3. 路由规则外置到 app/data/intent_routes.json（热加载，见 intent_routes.py），
   新增工具/关键词只改配置不改代码

【路由策略】
greeting    → 直接回复问候（不调用任何Agent）
rag_answer  → 交给RAG Agent（知识库检索+生成）
tool_call   → 交给Tool Agent（工具调用）
graph_query → Neo4j 图检索（实体关系）
goal        → 多步跨域编排
hybrid      → 先RAG后Tool（混合意图）
unclear     → 澄清追问

【设计思路】
规则层只保留高精度模式（Rule = 确定性短路，不是覆盖率主力）：
泛词大表（"库存""采购"等领域名词）会抢在语义层之前造成碰撞误判，
已移除；规则未命中的 query 下沉到语义层，由 rag_answer 样本承接。
============================================================
"""
import re
import logging
from enum import Enum
from typing import Optional
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm_router import LLMFactory
from app.core.semantic_router import get_semantic_router
from app.core.tool_engine import TOOL_REGISTRY
from app.core.intent_routes import get_intent_routes, ENTITY_CODE_RE

logger = logging.getLogger(__name__)

# 语义路由是否已初始化（延迟初始化，避免启动时 embedding 未就绪）
_semantic_ready = False


def _ensure_semantic_router():
    """延迟初始化语义路由（首次调用时从 RAGEngine 获取 embedding 引擎）"""
    global _semantic_ready
    if _semantic_ready:
        return
    try:
        from app.core.rag_engine import rag_engine
        sr = get_semantic_router()
        sr.init(rag_engine.embedding)
        _semantic_ready = True
        logger.info("[Router] 语义路由初始化完成")
    except Exception as e:
        logger.warning(f"[Router] 语义路由初始化失败，将跳过语义层: {e}")


class IntentType(str, Enum):
    """意图类型枚举"""
    GREETING = "greeting"       # 问候/闲聊
    RAG_ANSWER = "rag_answer"   # 知识库问答
    TOOL_CALL = "tool_call"     # 工具调用
    GRAPH_QUERY = "graph_query" # 图谱检索（实体关系匹配）
    GOAL = "goal"               # 目标型（多步跨域编排）
    HYBRID = "hybrid"           # 混合意图
    UNCLEAR = "unclear"         # 意图不明


# 图谱检索关键词（默认值，实际以 intent_routes.json 为准；与 graph_engine.py 保持同步）
GRAPH_KEYWORDS = [
    "哪些物料", "什么供应商", "影响的物料", "关联工单",
    "在途", "上游供应商", "缺货影响",
    "追溯", "延迟影响", "影响的订单",
]


class RouterAgent:
    """
    意图路由Agent

    工作流程：
    1. 接收用户输入
    2. 先尝试规则匹配（实体编码/命令词/问句形态，配置驱动）
    3. 规则未命中 → 语义路由（embedding 相似度）
    4. 语义未命中 → 调用LLM进行意图分类
    5. 返回意图类型 + 置信度
    """

    def __init__(self):
        # 问候正则（稳定模式，不需要配置化）
        self._greeting_patterns = [
            r"^(你好|嗨|hi|hello|hey|在吗|在不在)",
            r"^(谢谢|感谢|thanks|thank you)",
            r"^(再见|拜拜|bye|goodbye)",
        ]

    async def route(self, query: str) -> dict:
        """
        路由用户查询到对应的Agent

        Args:
            query: 用户输入文本

        Returns:
            {
                "intent": IntentType,       # 意图类型
                "confidence": float | None,  # 置信度（仅LLM分类返回，规则匹配为None）
                "tool_name": str | None,    # 如果是tool_call，指定工具名
                "method": str,              # 路由方法: "rule" / "llm"
            }

        【设计说明】
        规则匹配不返回confidence——规则是确定性的，不存在"信不信"的问题。
        只有LLM分类才需要confidence，因为模型输出本身有不确定性。
        RAG检索的confidence由rag_engine基于rerank_score计算（sigmoid映射），
        与路由器的confidence是不同维度，不要混淆。
        """
        # ---- Step 1: 规则匹配 ----
        rule_result = self._rule_match(query)
        if rule_result:
            logger.info(f"规则匹配命中: intent={rule_result['intent']}, query={query}")
            return rule_result

        # ---- Step 2: 语义路由（embedding 相似度，<10ms，零 token）----
        _ensure_semantic_router()
        if _semantic_ready:
            try:
                from app.core.rag_engine import rag_engine
                query_emb = rag_engine.embedding.embed_query(query)
                sr = get_semantic_router()
                semantic_result = sr.route(query_emb)
                if semantic_result:
                    logger.info(f"语义路由命中: intent={semantic_result.intent}, score={semantic_result.confidence:.3f}")
                    return {
                        "intent": IntentType(semantic_result.intent),
                        "tool_name": None,
                        "method": "semantic",
                        "confidence": semantic_result.confidence,
                    }
            except Exception as e:
                logger.warning(f"语义路由失败，回退LLM: {e}")

        # ---- Step 3: LLM 意图分类（~2.5s，兜底）----
        llm_result = await self._llm_classify(query)
        logger.info(f"LLM分类结果: intent={llm_result['intent']}, query={query}")
        return llm_result

    def _rule_match(self, query: str) -> Optional[dict]:
        """
        规则匹配：只做确定性短路（配置驱动，见 intent_routes.json）

        匹配顺序（高精度优先）：
        1. 问候（短 query 且无业务意图）
        2. 实体编码优先：含 MAT-/PO-/TK- 编码时，先判图谱关系词 → graph_query，
           再判领域提示词 → tool_call（修复 "MAT-001 还剩多少库存" 被泛词误判为 RAG）
        3. 精确工具命令词 → tool_call
        4. 目标型关键词 → goal
        5. 高精度知识问句正则 → rag_answer（问句形态，非领域泛词）

        未命中返回 None，下沉到语义路由层。
        """
        routes = get_intent_routes()
        query_lower = query.lower().strip()
        graph_keywords = routes.graph_keywords or GRAPH_KEYWORDS

        # 1. 问候匹配（仅当 query 短且不含业务意图时）
        for pattern in self._greeting_patterns:
            if re.match(pattern, query_lower):
                # 检查是否同时也包含业务意图（命令词/实体编码/目标词）
                has_business = (
                    any(kw in query for kw in routes.tool_commands)
                    or any(kw in query for kw in routes.goal_keywords)
                    or ENTITY_CODE_RE.search(query) is not None
                )
                # 短 query 或纯问候 → GREETING；长 query 含业务词 → 继续往下走
                if len(query) <= 10 and not has_business:
                    return {
                        "intent": IntentType.GREETING,
                        "tool_name": None,
                        "method": "rule",
                    }
                # 否则不返回，让后续匹配处理
                break

        # 2. 实体编码优先（含具体标识符的查询确定性最高）
        entity_match = ENTITY_CODE_RE.search(query)
        if entity_match:
            # 2a. 编码 + 关系词 → 图谱检索
            if any(kw in query for kw in graph_keywords):
                return {
                    "intent": IntentType.GRAPH_QUERY,
                    "tool_name": None,
                    "method": "rule",
                }
            # 2b. 编码前缀 + 领域提示词 → 对应工具
            #     （仅编码不强制 tool_call："MAT-001 的质检标准是什么" 应走 RAG）
            code_upper = entity_match.group(1).upper()
            for rule in routes.entity_rules:
                if code_upper.startswith(rule.prefix) and any(h in query for h in rule.hints):
                    return {
                        "intent": IntentType.TOOL_CALL,
                        "tool_name": rule.tool,
                        "method": "rule",
                    }

        # 3. 精确工具命令词
        for keyword, tool_name in routes.tool_commands.items():
            if keyword in query:
                return {
                    "intent": IntentType.TOOL_CALL,
                    "tool_name": tool_name,
                    "method": "rule",
                }

        # 4. 目标型关键词（需要多步跨域编排）
        for keyword in routes.goal_keywords:
            if keyword in query:
                return {
                    "intent": IntentType.GOAL,
                    "tool_name": None,
                    "method": "rule",
                }

        # 5. 高精度知识问句正则（问句形态，非领域泛词，避免碰撞误判）
        for pattern in routes.rag_patterns:
            if pattern.search(query):
                return {
                    "intent": IntentType.RAG_ANSWER,
                    "tool_name": None,
                    "method": "rule",
                }

        # 未命中任何规则 → 下沉语义路由层
        return None

    async def _llm_classify(self, query: str) -> dict:
        """
        LLM意图分类：当规则匹配未命中时，使用LLM理解语义

        1. Prompt设计是意图分类的关键，需要：
           - 明确列出所有可能的意图类别
           - 给出每个类别的判断标准
           - 要求LLM输出结构化结果（JSON）
        2. 这里的temperature设为0.1，因为分类任务需要确定性
        3. 实际生产中可以用微调的小模型替代（如MiniLM），
           速度更快、成本更低，但需要标注数据
        """
        system_prompt = """你是一个意图分类器。请判断用户输入属于以下哪个类别：

1. greeting - 问候、闲聊、寒暄（如"你好"、"你是谁"）
2. rag_answer - 需要知识库检索才能回答的问题（如"什么是RAG"、"如何部署Milvus"）
3. tool_call - 需要调用单个外部工具（如"深圳天气"、"计算3+5"、"今天几号"）
4. hybrid - 同时需要知识库和工具（如"深圳天气如何，适合户外运动吗"）
5. goal - 需要多步跨部门分析才能完成的目标（如"帮我评估库存短缺风险"、"分析供应商延迟影响"、"排查质量异常"）
6. graph_query - 含物料/订单/工单编码 + 实体关系词（如"MAT-001 缺货会影响哪些物料"、"PO-001 延迟影响什么"），走 Neo4j 图检索
7. unclear - 意图不明确，无法判断

graph_query 类别的特征：查询含具体物料/订单/工单编码（MAT-/PO-/TK-），同时出现了"影响""追溯""哪些""关联"等关系词。

请严格按以下JSON格式输出，不要输出其他内容：
{"intent": "类别", "confidence": 置信度(0-1), "tool_name": 工具名或null}

可用工具: query_inventory, query_order, query_supplier, create_ticket, get_datetime, get_knowledge"""

        llm = LLMFactory.get_llm(temperature=0.1, streaming=False)

        try:
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=query),
            ])

            # 解析LLM返回的JSON
            content = response.content.strip()

            # 尝试提取JSON（使用统一工具函数）
            from app.core.utils import parse_llm_json
            result = parse_llm_json(content)
            intent_str = result.get("intent", "unclear")

            # 验证意图类型是否合法
            try:
                intent = IntentType(intent_str)
            except ValueError:
                intent = IntentType.UNCLEAR

            # 校验 LLM 返回的 tool_name 是否在已注册工具中（防幻觉）
            raw_tool_name = result.get("tool_name")
            validated_tool = raw_tool_name if raw_tool_name in TOOL_REGISTRY else None
            if raw_tool_name and not validated_tool:
                logger.warning(f"[Router] LLM 幻觉工具名: {raw_tool_name}, 降级为 RAG")

            return {
                "intent": intent,
                "confidence": result.get("confidence", 0.5),
                "tool_name": validated_tool,
                "method": "llm",
            }

        except Exception as e:
            logger.error(f"LLM意图分类失败: {e}")

        # 兜底：返回unclear
        return {
            "intent": IntentType.UNCLEAR,
            "confidence": 0.3,
            "tool_name": None,
            "method": "llm",
        }


# 全局单例
router_agent = RouterAgent()
