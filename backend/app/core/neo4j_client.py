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
        SQLite 读取在独立线程执行（不阻塞事件循环），Neo4j 写入保持异步。
        启动时执行，使用 MERGE 保证幂等（重复执行不重复创建）。
        """
        import asyncio
        import os

        if not self._driver:
            return {"synced": False, "reason": "Neo4j 未连接"}

        db_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "supply_chain.db"
        )
        if not os.path.exists(db_path):
            logger.warning("SQLite 数据文件不存在: %s", db_path)
            return {"synced": False, "reason": "SQLite 数据文件不存在"}

        def _read_sqlite() -> dict:
            """在独立线程中读取 SQLite，避免阻塞事件循环"""
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            data = {}

            cur.execute(
                "SELECT default_code, name, qty_available, standard_price "
                "FROM product_product"
            )
            data["materials"] = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT partner_name FROM purchase_order")
            data["suppliers"] = [r["partner_name"] for r in cur.fetchall() if r["partner_name"]]

            cur.execute(
                "SELECT id, name, state, amount_total, partner_name "
                "FROM purchase_order"
            )
            data["orders"] = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT id, order_name, product_code, product_qty, "
                "       price_unit, price_subtotal, date_planned "
                "FROM purchase_order_line"
            )
            data["lines"] = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT sm.origin, sm.product_uom_qty, sm.state, "
                "       sm.date_expected, pp.default_code "
                "FROM stock_move sm "
                "JOIN product_product pp ON sm.product_id = pp.id"
            )
            data["moves"] = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT name, priority, description FROM maintenance_ticket"
            )
            data["tickets"] = [dict(r) for r in cur.fetchall()]

            conn.close()
            return data

        # 在线程池中执行 SQLite 读取
        data = await asyncio.get_running_loop().run_in_executor(None, _read_sqlite)

        stats = {"nodes": 0, "relations": 0}

        async with self._driver.session() as session:
            # 1. Material 物料节点
            for row in data["materials"]:
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

            # 2. Supplier 供应商节点
            for name in data["suppliers"]:
                await session.run(
                    """
                    MERGE (s:Supplier {name: $name})
                    SET s.code = coalesce(s.code, $code)
                    """,
                    name=name,
                    code=f"SUP-{name[:4].upper()}",
                )
                stats["nodes"] += 1

            # 3. PurchaseOrder 采购订单节点 + FROM 关系
            for row in data["orders"]:
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

            # 4. OrderLine 订单行节点 + CONTAINS/FOR 关系
            for row in data["lines"]:
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

            # 5. StockMove 在途节点 + HAS_MOVE 关系
            for row in data["moves"]:
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

            # 6. Ticket 工单节点
            for row in data["tickets"]:
                await session.run(
                    """
                    MERGE (t:Ticket {code: $code})
                    SET t.priority = $priority
                    """,
                    code=row["name"],
                    priority=row["priority"],
                )
                stats["nodes"] += 1

        logger.info(
            "📊 图谱同步完成: %d 节点, %d 关系", stats["nodes"], stats["relations"]
        )
        return {"synced": True, **stats}

    # ---- Graph RAG：2-hop 子图上下文检索 ----

    @staticmethod
    def _normalize_entity(entity: str) -> str:
        """实体拼写自愈归一化 (SuperPower-2)

        将模糊输入归一化为标准实体编码，支持：
        - MAT-OO1 / mat001 / MATOO1 → MAT-001（字母 O 纠正为数字 0）
        - po20250101 → PO-20250101（补充缺失的连字符）
        - MAT OO1（含空格）→ MAT-001

        设计原则：防御性编程，无论上游正则提取质量如何，
        此处做最后一层兜底归一化，保证 Neo4j Cypher 查询命中。
        """
        import re as _re
        normalized = entity.strip().upper()

        # 1. 去除中间空白（如 "MAT 001" → "MAT001"）
        normalized = _re.sub(r'\s+', '', normalized)

        # 2. 替换编码中字母 O/o 为数字 0（OCR/手误常见错误）
        #    匹配：前缀字母 + 可选连字符 + 含 O 的"数字"部分
        #    MAT-OO1 → MAT-001, MATOO1 → MAT001, SUP-OO1 → SUP-001
        def _fix_letter_o(m: _re.Match) -> str:
            prefix = m.group(1)  # 如 "MAT-" 或 "MAT"
            suffix = m.group(2)  # 如 "OO1"
            # 将后缀中的 O/o 替换为 0
            fixed_suffix = ''.join('0' if c in ('O',) else c for c in suffix)
            return prefix + fixed_suffix

        normalized = _re.sub(
            r'^(MAT|PO|SUP)(-?)([O0\d]+)$',
            lambda m: m.group(1) + m.group(2) + ''.join(
                '0' if c in ('O',) else c for c in m.group(3)
            ),
            normalized,
        )

        # 3. 补充缺失的连字符：MAT001 → MAT-001, PO20250101 → PO-20250101
        normalized = _re.sub(r'^(MAT|PO|SUP)(\d)', r'\1-\2', normalized)

        if normalized != entity.strip().upper():
            logger.info(
                f"[SelfHeal] 实体自愈归一化: '{entity.strip()}' → '{normalized}'"
            )

        return normalized

    async def get_2hop_subgraph_context(self, entity: str) -> str:
        """检索实体的 2-hop 关联子图，返回声明式自然语言描述

        用于 Graph RAG：将图谱三元组注入 RAG 检索的上下文 Chunk 中，
        弥补向量检索在“多跳实体关联”场景下的长尾召回不足。

        Args:
            entity: 实体标识符，如 MAT-001、PO-20250101、SUP-001

        Returns:
            声明式文本段落，如 "物料 MAT-001 由供应商 SUP-ABC 供应，
            关联采购订单 PO-001（状态: 待交付）"，未找到时返回空字符串
        """
        if not self._driver:
            return ""

        # SuperPower-2: 实体拼写自愈归一化（O→0, 补连字符, 去空白）
        entity = self._normalize_entity(entity)

        # 实体类型推断
        entity_type = "Material"
        if entity.upper().startswith("PO-") or entity.upper().startswith("PO"):
            entity_type = "PurchaseOrder"
        elif entity.upper().startswith("SUP-"):
            entity_type = "Supplier"

        statements = []
        try:
            async with self._driver.session() as session:
                if entity_type == "Material":
                    # (Material)-[:HAS_MOVE]->(StockMove)  +  (OrderLine)-[:FOR]->(Material)<-[:CONTAINS]-(PurchaseOrder)-[:FROM]->(Supplier)
                    result = await session.run("""
                        MATCH (m:Material {code: $entity})
                        OPTIONAL MATCH (m)-[:HAS_MOVE]->(sm:StockMove)
                        OPTIONAL MATCH (ol:OrderLine)-[:FOR]->(m)
                        OPTIONAL MATCH (po:PurchaseOrder)-[:CONTAINS]->(ol)
                        OPTIONAL MATCH (po)-[:FROM]->(s:Supplier)
                        RETURN m, sm, ol, po, s
                        LIMIT 5
                    """, entity=entity)
                    async for record in result:
                        if record["s"]:
                            statements.append(f"物料 {entity} 由供应商 {record['s'].get('name', '未知')} 供应")
                        if record["po"]:
                            po_state = record["po"].get("state", "未知")
                            statements.append(f"关联采购订单 {record['po'].get('code', '')}（状态: {po_state}）")
                        if record["sm"]:
                            sm_state = record["sm"].get("state", "未知")
                            statements.append(f"在途库存移库 {record['sm'].get('code', '')}（状态: {sm_state}，预期: {record['sm'].get('expected_date', '')}）")

                elif entity_type == "PurchaseOrder":
                    result = await session.run("""
                        MATCH (po:PurchaseOrder {code: $entity})
                        OPTIONAL MATCH (po)-[:FROM]->(s:Supplier)
                        OPTIONAL MATCH (po)-[:CONTAINS]->(ol:OrderLine)-[:FOR]->(m:Material)
                        RETURN po, s, ol, m
                        LIMIT 5
                    """, entity=entity)
                    async for record in result:
                        if record["s"]:
                            statements.append(f"采购订单 {entity} 来自供应商 {record['s'].get('name', '未知')}")
                        if record["m"]:
                            statements.append(f"包含物料 {record['m'].get('code', '')}（{record['m'].get('name', '')}），数量 {record['ol'].get('qty', '?')}")

                elif entity_type == "Supplier":
                    result = await session.run("""
                        MATCH (s:Supplier {name: $entity})
                        OPTIONAL MATCH (po:PurchaseOrder)-[:FROM]->(s)
                        OPTIONAL MATCH (po)-[:CONTAINS]->(ol:OrderLine)-[:FOR]->(m:Material)
                        RETURN s, po, m
                        LIMIT 5
                    """, entity=entity)
                    async for record in result:
                        if record["po"]:
                            statements.append(f"供应商 {entity} 有采购订单 {record['po'].get('code', '')}（状态: {record['po'].get('state', '未知')}）")
        except Exception as e:
            logger.warning(f"[GraphRAG] 2-hop 子图检索失败: {e}")
            return ""

        if statements:
            context = "；".join(statements) + "。"
            logger.info(f"[GraphRAG] entity={entity} type={entity_type} 召回 {len(statements)} 条关系")
            return context
        return ""


# 全局单例
neo4j_client = Neo4jClient()
