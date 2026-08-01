"""
SupplyChainRAG - 工具注册与实现模块（核心）
基于 ReAct 模式实现工具调用，为 Agent 层提供 10 个业务工具。

工具调用由 agents/tool.py 的 ToolAgent（手写 ReAct 循环）发起，
通过 TOOL_REGISTRY / get_all_tools() / get_tools_by_names() 获取工具定义。

业务数据来自本地 SQLite 模拟库（supply_chain.db），参考 Odoo 风格表结构：
  - product_product      物料主数据
  - purchase_order       采购订单头
  - purchase_order_line  采购订单行
  - stock_move           库存移动（在途，由 query_stock_move 查询）
  - maintenance_ticket   工单（create_ticket 写入 / query_ticket 查询）
  - res_partner          供应商主数据

所有工具均为 async，支持 I/O 并发调用。

--- Legacy ---
文件底部保留了 LangChain AgentExecutor 的旧实现，仅供参考，
当前生产环境不使用。
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite
from langchain_core.tools import BaseTool, tool

from app.core.data_filter import PIIFilter


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


@asynccontextmanager
async def _db_scope():
    """统一数据库连接生命周期（自动连接/关闭 + 异常日志）

    供新工具使用，消除每个工具重复的连接样板代码。
    用法：
        async with _db_scope() as conn:
            ...
    """
    conn = None
    try:
        conn = await _get_conn()
        yield conn
    except Exception as e:
        logger.error(f"[Tool][DB] 操作失败: {type(e).__name__}: {e}", exc_info=True)
        raise
    finally:
        if conn is not None:
            await conn.close()


def _safety_stock_for(qty: float) -> int:
    """安全库存统一口径（query_inventory 与 calculate_reorder_point 共用）

    实现：当前库存的 10%，至少 50 件。
    """
    return max(50, int(qty * 0.1))


# ---- L3 工具查询结果缓存（只读工具 read-through，写工具成功后失效）----

async def _l3_tool_cache(cache_key: str, loader) -> str:
    """只读工具查询结果的 L3 缓存包装（错误结果不缓存，Redis 不可用时直查）"""
    import hashlib

    from app.config import get_settings
    from app.core.cache_manager import cache_manager

    key_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
    return await cache_manager.l3_get_or_set(
        "tool",
        key_hash,
        get_settings().L3_CACHE_TTL_TOOL,
        loader,
        cache_if=lambda v: isinstance(v, str) and '"error"' not in v,
    )


async def _invalidate_tool_cache() -> None:
    """写操作成功后清空 tool 命名空间的 L3 缓存（防脏读）"""
    try:
        from app.core.cache_manager import cache_manager
        await cache_manager.l3_invalidate("tool")
    except Exception as e:
        logger.warning(f"[Tool] L3 缓存失效失败（不影响主流程）: {e}")


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
    return await _l3_tool_cache(
        f"query_inventory:{material_code}",
        lambda: _query_inventory_impl(material_code),
    )


async def _query_inventory_impl(material_code: str) -> str:
    """query_inventory 实际实现（被 L3 缓存包装）"""
    try:
        conn = await _get_conn()
    except Exception as e:
        logger.error(f"[query_inventory] 数据库连接失败: {type(e).__name__}: {e}")
        return json.dumps({"error": f"数据库连接失败，请稍后重试: {type(e).__name__}"}, ensure_ascii=False)

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
        safety_qty = _safety_stock_for(qty)

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
    except Exception as e:
        logger.error(f"[query_inventory] 查询失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps({"error": f"物料查询异常: {type(e).__name__} - {e}"}, ensure_ascii=False)
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
    return await _l3_tool_cache(
        f"query_order:{order_id}",
        lambda: _query_order_impl(order_id),
    )


async def _query_order_impl(order_id: str) -> str:
    """query_order 实际实现（被 L3 缓存包装）"""
    try:
        conn = await _get_conn()
    except Exception as e:
        logger.error(f"[query_order] 数据库连接失败: {type(e).__name__}: {e}")
        return json.dumps({"error": f"数据库连接失败，请稍后重试: {type(e).__name__}"}, ensure_ascii=False)

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
    except Exception as e:
        logger.error(f"[query_order] 查询失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps({"error": f"订单查询异常: {type(e).__name__} - {e}"}, ensure_ascii=False)
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
    try:
        conn = await _get_conn()
    except Exception as e:
        logger.error(f"[create_ticket] 数据库连接失败: {type(e).__name__}: {e}")
        return json.dumps({"error": f"数据库连接失败，请稍后重试: {type(e).__name__}"}, ensure_ascii=False)

    try:
        import random
        # 工单编码 = TK-秒级时间戳 + 7位随机后缀。
        # 随机后缀避免高频/紧循环调用在同一时钟 tick 内碰撞（Windows time_ns 分辨率粗，
        # 旧实现 time.time_ns()%%1e7 会重复→maintenance_ticket.name UNIQUE 冲突）。
        ticket_id = f"TK-{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(0, 9999999):07d}"
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

        # 写操作成功 → 失效只读工具的 L3 缓存，避免后续查询读到脏数据
        await _invalidate_tool_cache()

        return json.dumps({
            "ticket_id":   ticket_id,
            "title":       title,
            "description": description,
            "priority":    priority,
            "status":      "待处理",
            "created_at":  now,
        }, ensure_ascii=False)
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        logger.error(f"[create_ticket] 创建失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps({"error": f"工单创建异常: {type(e).__name__} - {e}"}, ensure_ascii=False)
    finally:
        await conn.close()


# ==========================================
# 工具 3.5：query_ticket — 查询工单状态（异步）
# ==========================================

@tool
async def query_ticket(ticket_id: str) -> str:
    """
    查询供应链工单的详细状态与处理进度。

    根据工单编号（如 TK-202506011234567）查询工单标题、优先级、描述、阶段、创建时间等信息。
    工单由 create_ticket 创建后写入本地 SQLite 数据库。

    参数: ticket_id - 工单编号（TK-开头）

    返回: JSON格式的工单数据
    """
    return await _l3_tool_cache(
        f"query_ticket:{ticket_id}",
        lambda: _query_ticket_impl(ticket_id),
    )


async def _query_ticket_impl(ticket_id: str) -> str:
    """query_ticket 实际实现（被 L3 缓存包装）"""
    if not ticket_id or not ticket_id.strip():
        return json.dumps(
            {"error": "工单编号不能为空，请提供 TK- 开头的工单编号"},
            ensure_ascii=False,
        )
    try:
        async with _db_scope() as conn:
            cur = await conn.execute(
                "SELECT name, priority, description, stage_id, user_id, "
                "create_date, write_date, date_deadline "
                "FROM maintenance_ticket WHERE name = ?",
                (ticket_id.strip(),)
            )
            row = await cur.fetchone()

            if not row:
                await cur.execute(
                    "SELECT name FROM maintenance_ticket "
                    "ORDER BY create_date DESC LIMIT 5"
                )
                recent = [r["name"] for r in await cur.fetchall()]
                return json.dumps(
                    {"error": f"未找到工单: {ticket_id}，最近工单: {recent}"},
                    ensure_ascii=False,
                )

            priority_map = {"0": "低", "1": "中", "2": "高", "3": "紧急"}
            stage_map = {0: "待处理", 1: "处理中", 2: "已完成", 3: "已关闭"}
            # create_ticket 写入时描述格式为 "{title}\n{description}"，首行即标题
            description = row["description"] or ""
            result = {
                "ticket_id": row["name"],
                "title": description.split("\n")[0] if description else "",
                "description": description,
                "priority": priority_map.get(str(row["priority"]), row["priority"]),
                "status": stage_map.get(row["stage_id"], str(row["stage_id"])),
                "stage_id": row["stage_id"],
                "user_id": row["user_id"],
                "created_at": row["create_date"],
                "updated_at": row["write_date"],
                "deadline": row["date_deadline"] or "—",
            }
            return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[query_ticket] 查询失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps(
            {"error": f"工单查询异常: {type(e).__name__} - {e}"},
            ensure_ascii=False,
        )


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
        result = await asyncio.to_thread(_get_rag_engine().search, query, top_k=3)
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
    return await _l3_tool_cache(
        f"query_supplier:{supplier_code}",
        lambda: _query_supplier_impl(supplier_code),
    )


async def _query_supplier_impl(supplier_code: str) -> str:
    """query_supplier 实际实现（被 L3 缓存包装）"""
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

            _pii = PIIFilter()
            return json.dumps(
                {
                    "supplier_code":    row["code"],
                    "name":             row["name"],
                    "category":         row["category"],
                    "contact":          row["contact"],
                    "phone":            _pii.filter_text(row["phone"]) if row["phone"] else "",
                    "email":            _pii.filter_text(row["email"]) if row["email"] else "",
                    "address":          _pii.filter_text(row["address"]) if row["address"] else "",
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
# 工具 7：track_logistics — 物流轨迹追踪
# ==========================================

@tool
async def track_logistics(po_code: str) -> str:
    """
    追踪采购订单的实时物流状态、轨迹节点及预测延误风险。

    参数:
      - po_code: 采购订单号，例如 PO-20250101

    返回: JSON格式的物流节点轨迹与时效预测
    """
    from datetime import datetime, timedelta

    try:
        # 归一化编码
        po_code = po_code.strip().upper()

        if not po_code or not po_code.startswith("PO-"):
            return json.dumps(
                {"error": f"无效订单号: {po_code}，请提供有效的 PO 编码（如 PO-20250101）"},
                ensure_ascii=False,
            )

        # 模拟数据源
        logistics_carriers = ["中远海运集运", "顺丰特快", "跨越速运", "德邦快递"]
        nodes_template = [
            {"node": "供应商发货完成", "location": "深圳盐田港"},
            {"node": "干线运输中", "location": "东海海域"},
            {"node": "到达枢纽港", "location": "上海洋山港"},
            {"node": "清关完成", "location": "上海港海关监管区"},
            {"node": "末端配送中", "location": "无锡分拨中心"},
            {"node": "已签收", "location": "SupplyChainRAG 工厂智能仓"},
        ]

        # 基于订单号确定性模拟（非随机，保证相同输入相同输出）
        seed = hash(po_code) % 1000
        depth = (seed % 4) + 2  # 2-5 个节点
        current_carrier = logistics_carriers[abs(hash(po_code)) % len(logistics_carriers)]
        actual_nodes = nodes_template[:depth]

        # 计算 ETA 延误率
        delay_probability = round((abs(hash(po_code)) % 40) / 100.0, 2)
        eta = (datetime.now() + timedelta(days=(6 - depth))).strftime("%Y-%m-%d %H:%M")

        result = {
            "po_code": po_code,
            "carrier": current_carrier,
            "current_status": actual_nodes[-1]["node"],
            "current_location": actual_nodes[-1]["location"],
            "nodes": actual_nodes,
            "eta": eta,
            "delay_risk_probability": f"{delay_probability * 100:.0f}%",
            "delay_warning": (
                "高风险延迟送达，建议安排备用物料调度！"
                if delay_probability > 0.25
                else "正常时效，暂无延误风险"
            ),
        }

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[track_logistics] 查询失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps({"error": f"物流追踪异常: {type(e).__name__} - {e}"}, ensure_ascii=False)


# ==========================================
# 工具 7.5：query_stock_move — 在途/收货记录查询（异步）
# ==========================================

@tool
async def query_stock_move(po_code: str) -> str:
    """
    查询采购订单的在途与收货记录（库存移动）。

    根据采购订单号（如 PO-20250602）查询关联的库存移动记录：物料、数量、源/目标库位、状态、预计到货日期、内部参考号。

    参数: po_code - 采购订单号（PO-开头）

    返回: JSON格式的在途/收货记录列表
    """
    return await _l3_tool_cache(
        f"query_stock_move:{po_code}",
        lambda: _query_stock_move_impl(po_code),
    )


async def _query_stock_move_impl(po_code: str) -> str:
    """query_stock_move 实际实现（被 L3 缓存包装）"""
    if not po_code or not po_code.strip():
        return json.dumps(
            {"error": "订单号不能为空，请提供 PO- 开头的订单号"},
            ensure_ascii=False,
        )
    try:
        async with _db_scope() as conn:
            cur = await conn.execute(
                "SELECT m.origin, m.product_uom_qty, m.location_id, m.location_dest_id, "
                "m.state, m.date_expected, m.date_done, m.reference, "
                "p.default_code, p.name AS product_name "
                "FROM stock_move m LEFT JOIN product_product p ON m.product_id = p.id "
                "WHERE m.origin = ?",
                (po_code.strip().upper(),)
            )
            rows = await cur.fetchall()
            if not rows:
                return json.dumps(
                    {"error": f"未找到订单 {po_code} 的在途/收货记录"},
                    ensure_ascii=False,
                )

            state_map = {
                "draft": "草稿", "confirmed": "已确认", "assigned": "已分配",
                "done": "已完成", "cancel": "已取消",
            }
            moves = []
            for r in rows:
                moves.append({
                    "po_code": r["origin"],
                    "material_code": r["default_code"],
                    "material_name": r["product_name"],
                    "qty": r["product_uom_qty"],
                    "from_location": r["location_id"],
                    "to_location": r["location_dest_id"],
                    "state": state_map.get(r["state"], r["state"]),
                    "expected_date": r["date_expected"],
                    "done_date": r["date_done"] or "—",
                    "reference": r["reference"] or "—",
                })
            return json.dumps(
                {"po_code": po_code.strip().upper(), "moves": moves, "count": len(moves)},
                ensure_ascii=False,
            )
    except Exception as e:
        logger.error(f"[query_stock_move] 查询失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps(
            {"error": f"在途查询异常: {type(e).__name__} - {e}"},
            ensure_ascii=False,
        )


# ==========================================
# 工具 8：calculate_reorder_point — 再订货点计算
# ==========================================

@tool
async def calculate_reorder_point(material_code: str) -> str:
    """
    根据供应链 ROP 模型，计算指定物料的再订货点（补货阈值），并提供智能补货建议。

    数学模型: ROP = (日均消耗 × 采购提前期) + 安全库存
    当当前库存低于 ROP 时，触发补货预警并建议联动 create_ticket 发起补货工单。

    参数:
      - material_code: 物料编码，例如 MAT-001

    返回: JSON格式的库存分析、安全库存参数及补货决策建议
    """
    from datetime import datetime

    # SuperPower-2: 实体拼写自愈归一化
    from app.core.neo4j_client import Neo4jClient
    material_code = Neo4jClient._normalize_entity(material_code)

    try:
        conn = await _get_conn()
    except Exception as e:
        logger.error(f"[calculate_reorder_point] 数据库连接失败: {type(e).__name__}: {e}")
        return json.dumps({"error": f"数据库连接失败，请稍后重试: {type(e).__name__}"}, ensure_ascii=False)

    try:
        # 1. 从 product_product 表获取当前真实库存
        cur = await conn.execute(
            "SELECT default_code, name, qty_available, incoming_qty, "
            "outgoing_qty, virtual_available, standard_price "
            "FROM product_product WHERE default_code = ?",
            (material_code,),
        )
        row = await cur.fetchone()

        if not row:
            await cur.execute("SELECT default_code FROM product_product ORDER BY default_code")
            available = [r["default_code"] for r in await cur.fetchall()]
            return json.dumps(
                {"error": f"未找到物料编码: {material_code}，可用编码: {available}"},
                ensure_ascii=False,
            )

        current_qty = row["qty_available"] or 0
        incoming_qty = row["incoming_qty"] or 0
        outgoing_qty = row["outgoing_qty"] or 0
        virtual_available = row["virtual_available"] or 0
        material_name = row["name"]

        # 2. 供应链计算参数（基于物料编号确定性推算，保证幂等）
        seed = abs(hash(material_code)) % 1000
        daily_consumption = (seed % 10) + 5       # 日均消耗: 5-14 件
        lead_time = (seed % 4) + 3                 # 采购提前期: 3-6 天
        safety_stock = _safety_stock_for(current_qty)  # 安全库存统一口径（与 query_inventory 一致）

        # 3. ROP 计算：ROP = (日均消耗 × 提前期) + 安全库存
        lead_time_demand = daily_consumption * lead_time
        reorder_point = lead_time_demand + safety_stock

        # 4. 补货决策
        needs_reorder = current_qty < reorder_point
        reorder_gap = reorder_point - current_qty if needs_reorder else 0
        suggested_reorder_qty = max(
            0,
            (reorder_point * 2 - current_qty) if needs_reorder else 0,
        )

        result = {
            "material_code": material_code,
            "material_name": material_name,
            "current_stock": current_qty,
            "incoming_qty": incoming_qty,
            "outgoing_qty": outgoing_qty,
            "virtual_available": virtual_available,
            "daily_consumption_pcs": daily_consumption,
            "lead_time_days": lead_time,
            "lead_time_demand_pcs": lead_time_demand,
            "safety_stock_pcs": safety_stock,
            "reorder_point_pcs": reorder_point,
            "needs_reorder": needs_reorder,
            "reorder_gap_pcs": reorder_gap,
            "suggested_reorder_qty_pcs": suggested_reorder_qty,
            "decision": (
                f"库存告急！当前库存 {current_qty} 件 低于再订货点 {reorder_point} 件（缺口 {reorder_gap} 件）。"
                f"建议立即通过 create_ticket 发起采购补货工单，推荐补货量 {suggested_reorder_qty} 件。"
                if needs_reorder
                else f"库存水位正常：当前 {current_qty} 件 >= 再订货点 {reorder_point} 件，无需补货。"
            ),
            "calculated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[calculate_reorder_point] 计算失败: {type(e).__name__}: {e}", exc_info=True)
        return json.dumps(
            {"error": f"ROP 计算异常: {type(e).__name__} - {e}"},
            ensure_ascii=False,
        )
    finally:
        await conn.close()


# ==========================================
# 通用工具
# ==========================================

@tool
async def web_search(query: str) -> str:
    """搜索互联网获取最新信息。用于知识库中没有的问题、实时数据查询。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
            )
            data = resp.json()
            results = []
            if data.get("AbstractText"):
                results.append(f"摘要: {data['AbstractText']}")
            for item in data.get("RelatedTopics", [])[:5]:
                if "Text" in item:
                    results.append(f"- {item['Text']}")
            if results:
                return "\n".join(results)
            return f"未找到关于「{query}」的搜索结果"
    except Exception as e:
        return f"搜索失败: {e}"


@tool
async def calculator(expression: str) -> str:
    """计算数学表达式。支持加减乘除、幂运算、括号。例: '1200 * 0.85 + 500'"""
    import ast
    import math
    import operator

    ALLOWED_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
    }

    ALLOWED_NAMES = {
        "pi": math.pi,
        "e": math.e,
        "sqrt": math.sqrt,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPS:
            return ALLOWED_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_OPS:
            return ALLOWED_OPS[type(node.op)](_eval(node.operand))
        elif isinstance(node, ast.Name) and node.id in ALLOWED_NAMES:
            return ALLOWED_NAMES[node.id]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ALLOWED_NAMES:
            fn = ALLOWED_NAMES[node.func.id]
            if callable(fn):
                return fn(*[_eval(a) for a in node.args])
        raise ValueError(f"不支持的表达式: {ast.dump(node)}")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        return str(result)
    except Exception as e:
        return f"计算失败: {e}"


@tool
async def code_interpreter(code: str) -> str:
    """在安全沙箱中执行 Python 代码。只允许 math/statistics/datetime/json/re/collections 模块，禁止文件/网络/进程操作。"""
    import ast
    import contextlib
    import io

    ALLOWED_MODULES = {"math", "statistics", "datetime", "json", "re", "collections"}

    # ---- AST 白名单预检查：拒绝危险节点 ----
    class _SafetyChecker(ast.NodeVisitor):
        """遍历 AST，拒绝 Import / Attribute（getattr 等）/ Global / Delete 节点"""

        def visit_Import(self, node):
            raise ValueError("安全限制: 禁止 import 语句，请在代码中直接使用已授权模块")

        def visit_ImportFrom(self, node):
            raise ValueError("安全限制: 禁止 from...import 语句，请在代码中直接使用已授权模块")

        _DANGEROUS_ATTRS = {
            "__import__", "__builtins__", "__subclasses__", "__globals__",
            "__code__", "__class__", "__bases__", "__mro__",
            "__init__", "__init_subclass__", "__reduce__", "__getattribute__",
            "system", "popen", "exec", "eval", "compile",
            "open", "read", "write", "remove", "unlink",
        }

        def visit_Attribute(self, node):
            # 只拦截危险属性访问，允许 math.sqrt 等安全调用
            if node.attr in self._DANGEROUS_ATTRS:
                raise ValueError(f"安全限制: 禁止访问危险属性 '{node.attr}'")
            self.generic_visit(node)

        def visit_Global(self, node):
            raise ValueError("安全限制: 禁止 global 声明")

        def visit_Delete(self, node):
            raise ValueError("安全限制: 禁止 del 操作")

    try:
        tree = ast.parse(code, mode="exec")
        _SafetyChecker().visit(tree)
    except (SyntaxError, ValueError) as e:
        return f"安全限制: {e}"

    # ---- 受限的 __import__：只允许白名单模块 ----
    def _safe_import(name, *args, **kwargs):
        if name in ALLOWED_MODULES:
            return __import__(name, *args, **kwargs)
        raise ImportError(f"安全限制: 禁止导入 {name}，只允许 {ALLOWED_MODULES}")

    # ---- 沙箱 builtins：只暴露安全的内置函数 ----
    import collections as _collections
    import datetime as _dt
    import math
    import re as _re_mod
    import statistics

    safe_builtins = {
        "__import__": _safe_import,
        "range": range, "len": len, "int": int, "float": float, "str": str,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "bool": bool,
        "print": lambda *a, **kw: print(*a, file=output, **kw),
        "sorted": sorted, "enumerate": enumerate, "zip": zip,
        "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
        "map": map, "filter": filter, "any": any, "all": all,
        "True": True, "False": False, "None": None,
    }

    # 预注入白名单模块，避免代码中需要 import
    local_vars = {
        "__builtins__": safe_builtins,
        "math": math,
        "statistics": statistics,
        "datetime": _dt,
        "json": json,
        "re": _re_mod,
        "collections": _collections,
    }

    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            exec(code, local_vars)
        result = output.getvalue()
        return result if result else "代码执行成功（无输出）"
    except Exception as e:
        return f"执行失败: {type(e).__name__}: {e}"


# ==========================================
# 工具注册表
# ==========================================

TOOL_REGISTRY: dict[str, BaseTool] = {
    "query_inventory":          query_inventory,
    "query_order":              query_order,
    "create_ticket":            create_ticket,
    "query_ticket":             query_ticket,
    "get_datetime":             get_datetime,
    "get_knowledge":            get_knowledge,
    "query_supplier":           query_supplier,
    "track_logistics":          track_logistics,
    "query_stock_move":         query_stock_move,
    "calculate_reorder_point":  calculate_reorder_point,
    "web_search":               web_search,
    "calculator":               calculator,
    "code_interpreter":         code_interpreter,
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
    """获取所有已注册工具（含 MCP 工具）"""
    tools = list(TOOL_REGISTRY.values())
    # 合并 MCP 工具
    try:
        from app.core.mcp_client import get_mcp_client
        mcp_tools = get_mcp_client().get_langchain_tools()
        tools.extend(mcp_tools)
    except Exception as e:
        logger.debug(f"[Tool] MCP工具加载失败: {e}")
    return tools


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

