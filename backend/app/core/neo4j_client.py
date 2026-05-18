"""
Neo4j 图数据库客户端 — 供应链实体关系图谱

启动时从 SQLite 全量同步节点和关系到 Neo4j（< 50 节点，< 1 秒），
提供 Cypher 模板查询用于 3 个跨域供应链场景的结构化推理。

依赖: neo4j (pip install neo4j), 需 Neo4j 5.x Community Edition 运行在 Docker
"""

import logging
from neo4j import AsyncGraphDatabase
from app.config import get_settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Neo4j 异步客户端 — 连接管理 + 数据同步 + 健康检查"""

    def __init__(self):
        self._driver = None
        self._connected = False

    # ---- 连接管理 ----

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """建立 Neo4j Bolt 连接"""
        settings = get_settings()
        try:
            self._driver = AsyncGraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                max_connection_lifetime=3600,
            )
            # 验证连接
            await self._driver.verify_connectivity()
            self._connected = True
            logger.info("✅ Neo4j 连接成功: %s", settings.NEO4J_URI)
            return True
        except Exception as e:
            self._connected = False
            logger.warning("⚠️ Neo4j 连接失败: %s（图谱检索不可用）", e)
            return False

    async def disconnect(self):
        """关闭 Neo4j 连接"""
        if self._driver:
            await self._driver.close()
            self._driver = None
        self._connected = False
        logger.info("Neo4j 连接已关闭")

    def get_session(self):
        """返回 Neo4j 异步 session 上下文管理器，封装连接健康检查"""
        if not self._driver or not self._connected:
            raise RuntimeError("Neo4j 未连接，无法获取 session")
        return self._driver.session()

    async def health(self) -> dict:
        """健康检查响应"""
        if not self._driver:
            return {"connected": False}
        try:
            await self._driver.verify_connectivity()
            return {"connected": True}
        except Exception:
            return {"connected": False}

    # ---- 数据同步（SQLite → Neo4j）----

    async def sync_from_sqlite(self) -> dict:
        """
        从 SQLite supply_chain.db 全量同步到 Neo4j。
        启动时执行，使用 MERGE 保证幂等（重复执行不重复创建）。
        """
        import sqlite3
        import os

        if not self._driver:
            return {"synced": False, "reason": "Neo4j 未连接"}

        db_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "supply_chain.db"
        )
        if not os.path.exists(db_path):
            logger.warning("SQLite 数据文件不存在: %s", db_path)
            return {"synced": False, "reason": "SQLite 数据文件不存在"}

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        stats = {"nodes": 0, "relations": 0}

        async with self._driver.session() as session:
            # 1. Material 物料节点
            cur.execute(
                "SELECT default_code, name, qty_available, standard_price "
                "FROM product_product"
            )
            for row in cur.fetchall():
                await session.run(
                    """
                    MERGE (m:Material {code: $code})
                    SET m.name = $name,
                        m.qty_available = $qty,
                        m.standard_price = $price
                    """,
                    code=row["default_code"],
                    name=row["name"],
                    qty=row["qty_available"],
                    price=row["standard_price"],
                )
                stats["nodes"] += 1

            # 2. Supplier 供应商节点（从 purchase_order 去重）
            cur.execute("SELECT DISTINCT partner_name FROM purchase_order")
            for row in cur.fetchall():
                if row["partner_name"]:
                    await session.run(
                        """
                        MERGE (s:Supplier {name: $name})
                        SET s.code = coalesce(s.code, $code)
                        """,
                        name=row["partner_name"],
                        code=f"SUP-{row['partner_name'][:4].upper()}",
                    )
                    stats["nodes"] += 1

            # 3. PurchaseOrder 采购订单节点
            cur.execute(
                "SELECT id, name, state, amount_total, partner_name "
                "FROM purchase_order"
            )
            for row in cur.fetchall():
                await session.run(
                    """
                    MERGE (po:PurchaseOrder {code: $code})
                    SET po.state = $state,
                        po.amount_total = $amount
                    """,
                    code=row["name"],
                    state=row["state"],
                    amount=row["amount_total"],
                )
                stats["nodes"] += 1
                # 关系：PurchaseOrder -[:FROM]-> Supplier
                if row["partner_name"]:
                    await session.run(
                        """
                        MATCH (po:PurchaseOrder {code: $po_code})
                        MATCH (s:Supplier {name: $sup_name})
                        MERGE (po)-[:FROM]->(s)
                        """,
                        po_code=row["name"],
                        sup_name=row["partner_name"],
                    )
                    stats["relations"] += 1

            # 4. OrderLine 订单行节点 + :CONTAINS/:FOR 关系
            cur.execute(
                "SELECT id, order_name, product_code, product_qty, "
                "       price_unit, price_subtotal, date_planned "
                "FROM purchase_order_line"
            )
            for row in cur.fetchall():
                line_code = f"{row['order_name']}-L{row['id']}"
                await session.run(
                    """
                    MERGE (ol:OrderLine {code: $code})
                    SET ol.qty = $qty,
                        ol.price_unit = $price,
                        ol.subtotal = $subtotal,
                        ol.date_planned = $planned
                    """,
                    code=line_code,
                    qty=row["product_qty"],
                    price=row["price_unit"],
                    subtotal=row["price_subtotal"],
                    planned=row["date_planned"],
                )
                stats["nodes"] += 1
                # 关系：PurchaseOrder -[:CONTAINS]-> OrderLine
                if row["order_name"]:
                    await session.run(
                        """
                        MATCH (po:PurchaseOrder {code: $po_code})
                        MATCH (ol:OrderLine {code: $line_code})
                        MERGE (po)-[:CONTAINS]->(ol)
                        """,
                        po_code=row["order_name"],
                        line_code=line_code,
                    )
                    stats["relations"] += 1
                # 关系：OrderLine -[:FOR]-> Material
                if row["product_code"]:
                    await session.run(
                        """
                        MATCH (ol:OrderLine {code: $line_code})
                        MATCH (m:Material {code: $mat_code})
                        MERGE (ol)-[:FOR]->(m)
                        """,
                        line_code=line_code,
                        mat_code=row["product_code"],
                    )
                    stats["relations"] += 1

            # 5. StockMove 在途节点 + :HAS_MOVE 关系
            cur.execute(
                "SELECT sm.origin, sm.product_uom_qty, sm.state, "
                "       sm.date_expected, pp.default_code "
                "FROM stock_move sm "
                "JOIN product_product pp ON sm.product_id = pp.id"
            )
            for row in cur.fetchall():
                move_code = f"MOVE-{row['origin']}-{row['default_code']}"
                await session.run(
                    """
                    MERGE (sm:StockMove {code: $code})
                    SET sm.qty = $qty,
                        sm.state = $state,
                        sm.expected_date = $expected
                    """,
                    code=move_code,
                    qty=row["product_uom_qty"],
                    state=row["state"],
                    expected=row["date_expected"],
                )
                stats["nodes"] += 1
                # 关系：Material -[:HAS_MOVE]-> StockMove
                if row["default_code"]:
                    await session.run(
                        """
                        MATCH (m:Material {code: $mat_code})
                        MATCH (sm:StockMove {code: $move_code})
                        MERGE (m)-[:HAS_MOVE]->(sm)
                        """,
                        mat_code=row["default_code"],
                        move_code=move_code,
                    )
                    stats["relations"] += 1

            # 6. Ticket 工单节点 + :RELATED_TO 关系
            cur.execute(
                "SELECT name, priority, description FROM maintenance_ticket"
            )
            for row in cur.fetchall():
                await session.run(
                    """
                    MERGE (t:Ticket {code: $code})
                    SET t.priority = $priority
                    """,
                    code=row["name"],
                    priority=row["priority"],
                )
                stats["nodes"] += 1
                # 关系无法自动推断（工单描述中没有物料编码），跳过

        conn.close()
        logger.info(
            "📊 图谱同步完成: %d 节点, %d 关系", stats["nodes"], stats["relations"]
        )
        return {"synced": True, **stats}


# 全局单例
neo4j_client = Neo4jClient()
