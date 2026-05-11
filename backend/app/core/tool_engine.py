"""
SmartQA Pro - 工具调用引擎
基于ReAct模式实现工具调用

【已废弃】本模块的 ToolEngine 类未被使用。
当前工具调用由 agents/tool.py 的 ToolAgent（手写ReAct循环）实现。

本文件保留作为LangChain Agent的参考实现，
如需使用LangChain的ReAct Agent，可启用 ToolEngine.get_agent()。

业务数据来自本地 SQLite 模拟库（supply_chain.db），参考 Odoo 风格表结构：
  - product_product    物料主数据
  - purchase_order     采购订单头
  - purchase_order_line 采购订单行
  - stock_move         库存移动（在途）
  - maintenance_ticket 工单

所有工具均为 async，支持 I/O 并发调用。
"""
import json
import os
import logging
from datetime import datetime
from typing import Optional
from langchain_core.tools import tool, BaseTool
import aiosqlite

# 延迟导入 rag_engine，避免循环引用（rag_engine 依赖 Milvus/Embedding 等重量级组件）
# 实际调用时才 import，确保工具模块可在测试环境中独立加载
def _get_rag_engine():
    from app.core.rag_engine import rag_engine as _re
    return _re

logger = logging.getLogger(__name__)

# ---- SQLite 数据库路径（相对于本文件位置）----
_DATA_DB = os.path.join(os.path.dirname(__file__), "..", "data", "supply_chain.db")


async def _get_conn() -> aiosqlite.Connection:
    """获取异步数据库连接"""
    conn = await aiosqlite.connect(_DATA_DB)
    conn.row_factory = aiosqlite.Row
    return conn


# ==========================================
# 工具 1：query_inventory — 物料库存查询（异步）
# ==========================================

@tool
async def query_inventory(material_code: str) -> str:
    """
    查询原材料/物料的库存信息。

    根据物料编码查询当前库存数量、安全库存、库存状态、在途数量等信息。
    库存状态：充足（现货 >= 安全库存 × 1.5）、预警（安全库存 ≤ 现货 < 安全库存 × 1.5）、不足（现货 < 安全库存）

    参数: material_code - 物料编码（如：MAT-001、MAT-002）

    返回: JSON格式的库存数据
    """
    conn = await _get_conn()
    try:
        cur = await conn.execute(
            "SELECT id, default_code, name, uom_id, standard_price, "
            "qty_available, virtual_available, incoming_qty, outgoing_qty "
            "FROM product_product WHERE default_code = ?",
            (material_code,)
        )
        row = await cur.fetchone()

        if not row:
            await cur.execute("SELECT default_code FROM product_product ORDER BY default_code")
            available = [r["default_code"] for r in await cur.fetchall()]
            return json.dumps(
                {"error": f"未找到物料编码: {material_code}，可用编码: {available}"},
                ensure_ascii=False
            )

        qty = row["qty_available"]
        incoming = row["incoming_qty"]
        safety_qty = max(50, int(qty * 0.1))

        if qty >= safety_qty * 1.5:
            status = "充足"
        elif qty >= safety_qty:
            status = "预警"
        else:
            status = "不足"

        uom_map = {1: "个", 3: "升", 4: "条", 5: "米"}
        unit = uom_map.get(row["uom_id"], "个")

        result = {
            "material_code":   row["default_code"],
            "name":            row["name"],
            "quantity":        qty,
            "unit":            unit,
            "safety_stock":    safety_qty,
            "incoming_qty":    incoming,
            "outgoing_qty":    row["outgoing_qty"],
            "standard_price":  row["standard_price"],
            "status":          status,
        }
        return json.dumps(result, ensure_ascii=False)
    finally:
        await conn.close()


# ==========================================
# 工具 2：query_order — 采购订单查询（异步）
# ==========================================

@tool
async def query_order(order_id: str) -> str:
    """
    查询采购订单的详细状态。

    根据订单编号查询采购订单的供应商、订单状态、物料明细、金额、预计到货日期等信息。

    参数: order_id - 采购订单号（如：PO-20250601、PO-20250602）

    返回: JSON格式的订单数据
    """
    conn = await _get_conn()
    try:
        cur = await conn.execute(
            "SELECT id, name, partner_name, date_order, date_approve, "
            "invoice_status, state, amount_total, notes "
            "FROM purchase_order WHERE name = ?",
            (order_id,)
        )
        order_row = await cur.fetchone()

        if not order_row:
            await cur.execute("SELECT name FROM purchase_order ORDER BY name DESC LIMIT 5")
            available = [r["name"] for r in await cur.fetchall()]
            return json.dumps(
                {"error": f"未找到订单: {order_id}，最近订单: {available}"},
                ensure_ascii=False
            )

        await cur.execute(
            "SELECT product_code, product_name, product_qty, price_unit, "
            "price_subtotal, date_planned, qty_received "
            "FROM purchase_order_line WHERE order_id = ?",
            (order_row["id"],)
        )
        lines = []
        for ln in await cur.fetchall():
            lines.append({
                "name":           ln["product_name"],
                "material_code":  ln["product_code"],
                "qty":            ln["product_qty"],
                "price":          ln["price_unit"],
                "subtotal":       ln["price_subtotal"],
                "planned_date":   ln["date_planned"],
                "received_qty":   ln["qty_received"],
            })

        state_map = {
            "draft":    "草稿",
            "purchase": "已确认",
            "done":     "已完成",
            "cancel":   "已取消",
        }

        result = {
            "order_id":       order_row["name"],
            "supplier":       order_row["partner_name"],
            "status":         state_map.get(order_row["state"], order_row["state"]),
            "raw_state":      order_row["state"],
            "order_date":     order_row["date_order"],
            "approve_date":   order_row["date_approve"] or "—",
            "invoice_status": order_row["invoice_status"],
            "items":          lines,
            "total_amount":   order_row["amount_total"],
            "notes":          order_row["notes"] or "—",
        }
        return json.dumps(result, ensure_ascii=False)
    finally:
        await conn.close()


# ==========================================
# 工具 3：create_ticket — 创建供应链工单（异步）
# ==========================================

@tool
async def create_ticket(title: str, description: str, priority: str) -> str:
    """
    创建供应链异常/需求工单。

    当发现库存不足、采购延误、质量异常等问题时，可创建工单进行跟踪处理。
    工单创建后会写入本地 SQLite 数据库。

    参数:
      - title: 工单标题（简要描述问题）
      - description: 工单详细描述
      - priority: 优先级（低/中/高/紧急）

    返回: JSON格式的工单确认信息
    """
    conn = await _get_conn()
    try:
        import time
        ticket_id = f"TK-{datetime.now().strftime('%Y%m%d%H%M%S')}{time.time_ns() % 100000:05d}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        priority_map = {"低": "0", "中": "1", "高": "2", "紧急": "3"}
        priority_val = priority_map.get(priority, "1")

        await conn.execute(
            "INSERT INTO maintenance_ticket "
            "(name, priority, description, stage_id, user_id, create_date, write_date) "
            "VALUES (?, ?, ?, 0, 1, ?, ?)",
            (ticket_id, priority_val, f"{title}\n{description}", now, now)
        )
        await conn.commit()

        return json.dumps({
            "ticket_id":   ticket_id,
            "title":       title,
            "description": description,
            "priority":    priority,
            "status":      "待处理",
            "created_at":  now,
        }, ensure_ascii=False)
    finally:
        await conn.close()


# ==========================================
# 工具 4：get_datetime — 获取当前时间（异步）
# ==========================================

@tool
async def get_datetime(unused: str = "") -> str:
    """
    获取当前日期时间。直接返回服务器真实当前时间，不需要任何参数。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==========================================
# 工具 5：get_knowledge — 知识库检索（由 RAG 引擎代理）
# ==========================================

@tool
async def get_knowledge(query: str) -> str:
    """
    Search the uploaded document knowledge base via RAG hybrid retrieval.

    Uses the configured embedding model (BAAI/bge-small-zh-v1.5) to perform
    semantic search and returns the top-3 most relevant chunks.
    Falls back to a friendly "not found" message when no results are retrieved.
    """
    if not query:
        return json.dumps({"query": "", "answer": "Please provide a query.", "chunks": []}, ensure_ascii=False)

    try:
        result = _get_rag_engine().search(query, top_k=3)
        chunks = result.get("results", [])
        if not chunks:
            return json.dumps({
                "query": query,
                "answer": "No relevant documents found in the knowledge base.",
                "chunks": [],
            }, ensure_ascii=False)

        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("source", "unknown")
            content = chunk.get("content", "")[:200]
            context_parts.append(f"[{i}] {source}: {content}...")

        answer = "Knowledge base search results:\n" + "\n".join(context_parts)
        return json.dumps({
            "query": query,
            "answer": answer,
            "chunks": [
                {"source": c.get("source", ""), "content": c.get("content", "")[:300]}
                for c in chunks
            ],
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "query": query,
            "answer": f"Knowledge base search failed (may not be initialized): {e}",
            "chunks": [],
        }, ensure_ascii=False)


# ==========================================
# 工具 6：query_supplier — 供应商信息查询（异步）
# ==========================================

@tool
async def query_supplier(supplier_code: str) -> str:
    """
    查询供应商基本信息与资质评级。

    根据供应商编码（如 SUP-001）查询供应商名称、类别、联系人、信用等级、
    资质认证、账期条款、平均交期、合作年限等信息。
    用于供应商准入评估、合规审查等场景。
    """
    try:
        conn = await _get_conn()
        try:
            # 精确查询
            cur = await conn.execute(
                """
                SELECT code, name, category, contact, phone, email, address,
                       credit_level, qualification, payment_terms,
                       lead_time_days, cooperation_since, notes
                FROM res_partner
                WHERE code = ?
                """,
                (supplier_code,),
            )
            row = await cur.fetchone()

            if row is None:
                # 返回可用供应商编码列表，方便调用方纠错
                cur2 = await conn.execute(
                    "SELECT code, name FROM res_partner ORDER BY code LIMIT 10"
                )
                available = await cur2.fetchall()
                available_str = ", ".join(f"{r['code']}({r['name']})" for r in available)
                return json.dumps(
                    {"error": f"供应商 {supplier_code} 不存在，可用编码: {available_str}"},
                    ensure_ascii=False,
                )

            # 合作年数计算
            import datetime as _dt
            since = row["cooperation_since"] or ""
            cooperation_years = ""
            if since:
                try:
                    since_year = int(since[:4])
                    cooperation_years = str(_dt.date.today().year - since_year)
                except ValueError:
                    pass

            return json.dumps(
                {
                    "supplier_code":    row["code"],
                    "name":             row["name"],
                    "category":         row["category"],
                    "contact":          row["contact"],
                    "phone":            row["phone"],
                    "email":            row["email"],
                    "address":          row["address"],
                    "credit_level":     row["credit_level"],
                    "qualification":    row["qualification"],
                    "payment_terms":    row["payment_terms"],
                    "lead_time_days":   row["lead_time_days"],
                    "cooperation_since": since,
                    "cooperation_years": cooperation_years,
                    "notes":            row["notes"] or "",
                },
                ensure_ascii=False,
            )
        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"[query_supplier] 查询失败: {e}", exc_info=True)
        return json.dumps({"error": f"供应商查询异常: {e}"}, ensure_ascii=False)


# ==========================================
# 工具注册表
# ==========================================

TOOL_REGISTRY: dict[str, BaseTool] = {
    "query_inventory": query_inventory,
    "query_order":     query_order,
    "create_ticket":   create_ticket,
    "get_datetime":    get_datetime,
    "get_knowledge":   get_knowledge,
    "query_supplier":  query_supplier,
}

# ==========================================
# 新增工具示例（注册一个新工具只需5行代码）
# ==========================================
# """示例：注册一个「查询物料成本」工具"""
# @tool("query_cost")
# async def query_cost(material_code: str) -> str:
#     """根据物料编码查询成本数据"""
#     conn = await _get_conn()
#     # 执行SQL查询...
#     return json.dumps(result)
#
# # 在 TOOL_REGISTRY 添加一行即可接入系统:
# # "query_cost": query_cost,
# #
# # 前端自动展示新工具，权限在 ROLE_TOOLS 中添加，
# # LLM Agent 在执行 ReAct 循环时自动发现新工具。
# ==========================================


def get_all_tools() -> list[BaseTool]:
    """获取所有已注册工具"""
    return list(TOOL_REGISTRY.values())


def get_tools_by_names(names: list[str]) -> list[BaseTool]:
    """根据名称获取工具"""
    return [TOOL_REGISTRY[name] for name in names if name in TOOL_REGISTRY]


# ==========================================
# 【废弃】LangChain Agent 实现保留在此
# ==========================================

try:
    from langchain.agents import AgentExecutor, create_react_agent
except ImportError:
    AgentExecutor = None
    create_react_agent = None

from langchain_core.prompts import PromptTemplate
from langchain_core.agents import AgentFinish
from app.core.llm_router import LLMFactory
