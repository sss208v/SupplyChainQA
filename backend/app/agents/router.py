"""
SmartQA Pro - 意图路由Agent
============================================================
1. 意图路由是Multi-Agent系统的"大脑"，负责判断用户问题该交给哪个Agent处理
2. 本实现采用两种策略：
   - 规则匹配：快速、零成本，适合明确模式（如"你好"、"天气"）
   - LLM分类：语义理解能力强，适合模糊意图
3. 路由结果决定后续调用链路，是整个系统的入口

【路由策略】
greeting   → 直接回复问候（不调用任何Agent）
rag_answer → 交给RAG Agent（知识库检索+生成）
tool_call  → 交给Tool Agent（工具调用）
hybrid     → 先RAG后Tool（混合意图）
unclear    → 澄清追问

【设计思路】
先规则后模型（Rule-First）：规则匹配0延迟，LLM作为兜底。
这也是企业级系统的常见做法——能用规则的不要浪费Token。
============================================================
"""
import re
import logging
from enum import Enum
from typing import Optional
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm_router import LLMFactory
from app.core.semantic_router import get_semantic_router

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """意图类型枚举"""
    GREETING = "greeting"       # 问候/闲聊
    RAG_ANSWER = "rag_answer"   # 知识库问答
    TOOL_CALL = "tool_call"     # 工具调用
    GRAPH_QUERY = "graph_query" # 图谱检索（实体关系匹配）
    GOAL = "goal"               # 目标型（多步跨域编排）
    HYBRID = "hybrid"           # 混合意图
    UNCLEAR = "unclear"         # 意图不明


# 图谱检索关键词（模块级，与 graph_engine.py 保持同步）
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
    2. 先尝试规则匹配（关键词/正则）
    3. 规则未命中 → 调用LLM进行意图分类
    4. 返回意图类型 + 置信度
    """

    def __init__(self):
        # ---- 规则匹配关键词表 ----
        # 每个意图对应一组关键词/正则，命中即返回
        # 优点：零延迟、零Token消耗
        # 缺点：无法理解语义，"深圳热不热"无法匹配到天气工具
        self._greeting_patterns = [
            r"^(你好|嗨|hi|hello|hey|在吗|在不在)",
            r"^(谢谢|感谢|thanks|thank you)",
            r"^(再见|拜拜|bye|goodbye)",
        ]

        # 工具关键词：出现这些词大概率需要调用工具
        self._tool_keywords = {
            # 供应链工具（高优先级：含具体标识符的查询）
            "查库存": "query_inventory", "库存查询": "query_inventory",
            "查一下物料": "query_inventory", "查物料": "query_inventory",
            "库存不够": "query_inventory", "库存不足": "query_inventory",
            "库存预警": "query_inventory", "补货": "query_inventory",
            "查订单": "query_order", "订单查询": "query_order",
            "采购单": "query_order", "到货了吗": "query_order",
            "建工单": "create_ticket", "创建工单": "create_ticket",
            "提工单": "create_ticket", "申请工单": "create_ticket",
            # 时间日期
            "几点": "get_datetime", "日期": "get_datetime", "今天几号": "get_datetime",
            "现在几点": "get_datetime", "今天是": "get_datetime", "星期几": "get_datetime",
            "几月几号": "get_datetime", "今天日期": "get_datetime", "现在时间": "get_datetime",
            "当前时间": "get_datetime", "今天星期": "get_datetime",
            # 供应商
            "供应商信息": "query_supplier", "查供应商": "query_supplier",
        }

        # RAG关键词：出现这些词大概率需要知识库检索
        self._rag_keywords = [
            # 概念类
            "什么是", "什么叫", "怎么理解", "原理", "介绍", "解释",
            "是什么", "指的是", "是用来", "有哪些", "都有什么",
            # 供应链场景
            "供应商", "采购", "库存", "质检", "物流", "仓储", "物料",
            "准入", "资质", "制度", "规范", "流程", "安全库存", "呆滞",
            "计算公式", "怎么算", "如何计算",
            "IQC", "BOM", "ERP", "MRP", "工单", "验收", "抽检",
            "来料", "出库", "入库", "盘点", "编码", "绩效", "评估",
            # 比较类
            "区别", "对比", "比较", "差异", "不同", "优劣", "优缺点",
            "哪个好", "有什么不同", "差别",
            # 实现类
            "如何实现", "怎么实现", "实现原理", "工作原理", "机制",
            "怎么用", "如何使用", "怎么配置", "怎么部署", "怎么安装",
            # 原因类
            "为什么", "为何", "原因", "道理", "怎么会",
            # 文档/教程
            "文档", "教程", "指南", "手册", "说明书", "文档地址",
            "怎么写", "代码", "示例", "demo", "例子",
            # 架构/设计
            "架构", "设计", "结构", "组成", "模块", "组件",
            # 技术名词
            "API", "SDK", "配置", "部署", "安装", "错误", "报错",
            "参数", "设置", "连接", "创建", "删除", "修改", "更新",
            "版本", "升级", "迁移", "导入", "导出", "接口",
            "数据库", "缓存", "队列", "集群", "分布式", "容器",
            "k8s", "docker", "redis", "milvus", "postgres", "mysql",
            "前端", "后端", "全栈", "移动端", "web", "app",
        ]

        # 目标型关键词：需要多步跨域编排（注意：必须在 RAG 关键词之后检查，避免误判）
        self._goal_keywords = [
            "帮我评估", "帮我分析", "帮我看看", "帮我判断",
            "要不要", "需不需要", "是否需要", "该不该",
            "怎么办", "怎么处理", "怎么应对",
            "影响评估", "风险评估", "缺口分析",
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

        # ---- Step 2: LLM意图分类 ----
        llm_result = await self._llm_classify(query)
        logger.info(f"LLM分类结果: intent={llm_result['intent']}, query={query}")
        return llm_result

    def _rule_match(self, query: str) -> Optional[dict]:
        """
        规则匹配：基于关键词和正则的快速路由

        规则匹配是NLP领域的经典方法，在深度学习之前是主流方案。
        即使在今天，企业系统仍然大量使用规则匹配，因为：
        - 确定性高（LLM有幻觉风险）
        - 零延迟
        - 零成本（不消耗API Token）
        """
        query_lower = query.lower().strip()

        # 1. 问候匹配（仅当 query 短且不含业务关键词时）
        for pattern in self._greeting_patterns:
            if re.match(pattern, query_lower):
                # 检查是否同时也包含业务意图
                has_business = any(kw in query for kw in self._tool_keywords) or \
                               any(kw in query for kw in self._rag_keywords)
                # 短 query 或纯问候 → GREETING；长 query 含业务词 → 继续往下走
                if len(query) <= 10 and not has_business:
                    return {
                        "intent": IntentType.GREETING,
                        "tool_name": None,
                        "method": "rule",
                    }
                # 否则不返回，让后续匹配处理
                break

        # 2. 工具关键词优先（精确匹配 > 模糊匹配）
        tool_matched = None
        for keyword, tool_name in self._tool_keywords.items():
            if keyword in query:
                tool_matched = tool_name
                break

        if tool_matched:
            return {
                "intent": IntentType.TOOL_CALL,
                "tool_name": tool_matched,
                "method": "rule",
            }

        # 3. 图谱检索关键词（含实体编码的结构化查询）
        # 提取实体后判断：有 MAT-/PO-/TK- 编码 + 关联意图词
        _entity_code = re.search(
            r"(MAT-\d+|PO-\d+|TK-\d+)", query, re.IGNORECASE
        )
        if _entity_code and any(kw in query for kw in GRAPH_KEYWORDS):
            return {
                "intent": IntentType.GRAPH_QUERY,
                "tool_name": None,
                "method": "rule",
            }

        # 4. 目标型关键词（需要多步跨域编排）
        for keyword in self._goal_keywords:
            if keyword in query:
                return {
                    "intent": IntentType.GOAL,
                    "tool_name": None,
                    "method": "rule",
                }

        # 4. RAG 关键词匹配（工具和目标都未命中时）
        for keyword in self._rag_keywords:
            if keyword in query:
                return {
                    "intent": IntentType.RAG_ANSWER,
                    "tool_name": None,
                    "method": "rule",
                }

        # 未命中任何规则
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
            import json
            content = response.content.strip()

            # 尝试提取JSON（LLM可能输出Markdown代码块包裹的JSON）
            json_match = re.search(r'\{[^}]+\}', content)
            if json_match:
                result = json.loads(json_match.group())
                intent_str = result.get("intent", "unclear")

                # 验证意图类型是否合法
                try:
                    intent = IntentType(intent_str)
                except ValueError:
                    intent = IntentType.UNCLEAR

                return {
                    "intent": intent,
                    "confidence": result.get("confidence", 0.5),
                    "tool_name": result.get("tool_name"),
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
