"""
SmartQA Pro - 工具调用引擎
基于ReAct模式实现工具调用

【已废弃】本模块的 ToolEngine 类未被使用。
当前工具调用由 agents/tool.py 的 ToolAgent（手写ReAct循环）实现。

本文件保留作为LangChain Agent的参考实现，
如需使用LangChain的ReAct Agent，可启用 ToolEngine.get_agent()。
"""
import logging
import json
from typing import Optional
from datetime import datetime
from langchain_core.tools import tool, BaseTool
from langchain_core.agents import AgentFinish
try:
    from langchain.agents import AgentExecutor, create_react_agent
except ImportError:
    # LangChain 1.x+: imports moved
    AgentExecutor = None
    create_react_agent = None
from langchain_core.prompts import PromptTemplate
from app.core.llm_router import LLMFactory

logger = logging.getLogger(__name__)


# ==========================================
# 工具定义
# ==========================================

@tool
def query_inventory(material_code: str) -> str:
    """
    查询原材料/物料的库存信息。

    根据物料编码查询当前库存数量、安全库存、库存状态等信息。
    库存状态：充足（库存>=安全库存*1.5）、预警（安全库存<=库存<安全库存*1.5）、不足（库存<安全库存）

    参数: material_code - 物料编码（如：MAT-001、MAT-002）

    返回: JSON格式的库存数据，包含 material_code, name, quantity, unit, safety_stock, status
    """
    import json
    inventory_db = {
        "MAT-001": {"name": "电机轴承6205", "quantity": 1500, "unit": "个", "safety_stock": 500},
        "MAT-002": {"name": "液压油32#", "quantity": 80, "unit": "升", "safety_stock": 200},
        "MAT-003": {"name": "不锈钢螺栓M10", "quantity": 3000, "unit": "个", "safety_stock": 1000},
        "MAT-004": {"name": "传送带皮带", "quantity": 15, "unit": "条", "safety_stock": 20},
        "MAT-005": {"name": "PLC控制器模块", "quantity": 5, "unit": "个", "safety_stock": 10},
    }
    item = inventory_db.get(material_code)
    if not item:
        return json.dumps({"error": f"未找到物料编码: {material_code}，可用编码: {list(inventory_db.keys())}"}, ensure_ascii=False)
    qty, ss = item["quantity"], item["safety_stock"]
    status = "充足" if qty >= ss * 1.5 else ("预警" if qty >= ss else "不足")
    return json.dumps({"material_code": material_code, **item, "status": status}, ensure_ascii=False)


@tool
def query_order(order_id: str) -> str:
    """
    查询采购订单的详细状态。

    根据订单编号查询采购订单的供应商、订单状态、物料明细、金额、预计到货日期等信息。

    参数: order_id - 采购订单号（如：PO-20250101、PO-20250102）

    返回: JSON格式的订单数据，包含 order_id, supplier, status, items, total_amount, expected_date
    """
    import json
    order_db = {
        "PO-20250101": {
            "supplier": "东莞精密轴承有限公司",
            "status": "已发货",
            "items": [{"name": "电机轴承6205", "qty": 500, "price": 15.0}],
            "total_amount": 7500.00,
            "expected_date": "2025-01-15",
        },
        "PO-20250102": {
            "supplier": "广州液压器材厂",
            "status": "待审批",
            "items": [{"name": "液压油32#", "qty": 500, "price": 28.0}],
            "total_amount": 14000.00,
            "expected_date": "2025-02-01",
        },
        "PO-20250103": {
            "supplier": "深圳传动设备科技",
            "status": "已完成",
            "items": [
                {"name": "传送带皮带", "qty": 10, "price": 350.0},
                {"name": "PLC控制器模块", "qty": 5, "price": 1200.0},
            ],
            "total_amount": 9500.00,
            "expected_date": "2025-01-10",
        },
    }
    order = order_db.get(order_id)
    if not order:
        return json.dumps({"error": f"未找到订单: {order_id}，可用订单: {list(order_db.keys())}"}, ensure_ascii=False)
    return json.dumps({"order_id": order_id, **order}, ensure_ascii=False)


@tool
def create_ticket(title: str, description: str, priority: str) -> str:
    """
    创建供应链异常/需求工单。

    当发现库存不足、采购延误、质量异常等问题时，可创建工单进行跟踪处理。

    参数:
      - title: 工单标题（简要描述问题）
      - description: 工单详细描述
      - priority: 优先级（低/中/高/紧急）

    返回: JSON格式的工单确认信息，包含 ticket_id, title, status, created_at
    """
    import json
    from datetime import datetime
    ticket_id = f"TK-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    return json.dumps({
        "ticket_id": ticket_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": "待处理",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False)


@tool
def get_datetime(unused: str = "") -> str:
    """
    获取当前日期时间。直接返回当前时间，不需要任何参数。
    无论传入什么参数，都返回真实当前时间。
    """
    from datetime import datetime as dt
    return dt.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def get_knowledge(query: str) -> str:
    """从知识库中检索信息。参数: query - 检索关键词"""
    return f"正在从知识库检索: {query}"


# ==========================================
# 工具注册表
# ==========================================

TOOL_REGISTRY: dict[str, BaseTool] = {
    "query_inventory": query_inventory,
    "query_order": query_order,
    "create_ticket": create_ticket,
    "get_datetime": get_datetime,
    "get_knowledge": get_knowledge,
}


def get_all_tools() -> list[BaseTool]:
    """获取所有已注册工具"""
    return list(TOOL_REGISTRY.values())


def get_tools_by_names(names: list[str]) -> list[BaseTool]:
    """根据名称获取工具"""
    return [TOOL_REGISTRY[name] for name in names if name in TOOL_REGISTRY]


# ==========================================
# ReAct Agent Prompt
# 【废弃】LangChain版本的ReAct实现，保留作为参考
# ==========================================

REACT_PROMPT_TEMPLATE = """你是一个智能助手，可以使用工具来回答用户的问题。

你可以使用以下工具:
{tools}

请严格按照以下格式回答:

Question: 用户的问题
Thought: 你应该思考要做什么
Action: 要使用的工具名称（必须是 [{tool_names}] 中的一个）
Action Input: 工具的输入参数
Observation: 工具返回的结果
... (Thought/Action/Action Input/Observation 可以重复多次)
Thought: 我已经知道最终答案
Final Answer: 对用户问题的最终回答

重要规则:
1. 如果问题不需要使用工具，直接给出Final Answer
2. 如果需要使用工具，严格按照Thought -> Action -> Action Input的顺序
3. 工具名称必须是上述列表中的一个
4. 仔细分析Observation的结果，决定是继续使用工具还是给出最终答案

开始!

Question: {input}
{agent_scratchpad}"""


class ToolEngine:
    """
    工具调用引擎【已废弃】
    
    本类的 get_agent() 方法创建的 LangChain ReAct AgentExecutor 从未被调用。
    当前工具调用逻辑由 agents/tool.py 的 ToolAgent 使用手写ReAct循环实现。
    
    保留原因：
    1. 展示LangChain Agent与传统手写Agent的区别
    2. 作为未来可能切换到LangChain Agent的参考
    3. 面试时可解释"为什么选择手写ReAct而不是LangChain"
    """

    def __init__(self):
        self._agent_executor: Optional[AgentExecutor] = None

    def get_agent(self, tools: Optional[list[BaseTool]] = None) -> AgentExecutor:
        """获取LangChain ReAct Agent（已废弃，请使用agents/tool.py的ToolAgent）"""
        logger.warning("ToolEngine.get_agent() 已废弃，请使用 agents/tool.py 的 ToolAgent")
        if self._agent_executor is not None:
            return self._agent_executor

        tools = tools or get_all_tools()
        llm = LLMFactory.get_llm(streaming=False)

        prompt = PromptTemplate.from_template(REACT_PROMPT_TEMPLATE)

        if create_react_agent is None:
            raise RuntimeError("LangChain ReAct agent 不可用，请使用 agents/tool.py")

        agent = create_react_agent(
            llm=llm,
            tools=tools,
            prompt=prompt,
        )

        self._agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            max_iterations=5,
            handle_parsing_errors=True,
        )

        return self._agent_executor

    async def run(self, query: str, tools: Optional[list[str]] = None) -> dict:
        """
        执行工具调用【已废弃】
        
        实际使用 agents/tool.py 的 tool_agent.run()
        """
        logger.warning("ToolEngine.run() 已废弃，请使用 agents/tool.py 的 tool_agent.run()")
        tool_list = get_tools_by_names(tools) if tools else get_all_tools()
        agent = self.get_agent(tool_list)

        try:
            result = await agent.ainvoke({"input": query})
            return {
                "answer": result.get("output", ""),
                "tool_calls": result.get("intermediate_steps", []),
            }
        except Exception as e:
            logger.error(f"工具调用失败: {e}")
            return {
                "answer": f"工具调用过程中出现错误: {e}",
                "tool_calls": [],
            }


# 全局单例（已废弃）
tool_engine = ToolEngine()
