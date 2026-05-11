"""
供应链模拟数据库初始化脚本
参考 Odoo 风格字段命名：product_product / stock_move / purchase_order / purchase_order_line / maintenance_ticket
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "supply_chain.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构与种子数据"""
    conn = get_conn()
    cur = conn.cursor()

    # ---- 1. 物料主数据（Odoo: product_product）----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_product (
        id               INTEGER PRIMARY KEY,
        default_code      TEXT UNIQUE NOT NULL,   -- 物料编码
        name             TEXT NOT NULL,            -- 物料名称
        categ_id         INTEGER,                  -- 分类ID
        uom_id           INTEGER,                  -- 单位（1=个，3=升，4=条，5=米）
        standard_price   REAL DEFAULT 0,           -- 标准单价
        qty_available   REAL DEFAULT 0,          -- 现货库存（Odoo: qty_available）
        virtual_available REAL DEFAULT 0,          -- 虚拟库存
        incoming_qty     REAL DEFAULT 0,          -- 在途库存（Odoo: incoming_qty）
        outgoing_qty    REAL DEFAULT 0,           -- 已分配
        create_date      TEXT,
        write_date       TEXT
    )
    """)

    # ---- 2. 库存移动（Odoo: stock_move）----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_move (
        id                INTEGER PRIMARY KEY,
        origin            TEXT,                     -- 源单据（PO-xxx）
        product_id        INTEGER,                   -- 关联 product_product.id
        product_uom_qty   REAL DEFAULT 0,           -- 数量
        location_id       INTEGER,                   -- 源库位（8=仓库，9=虚拟）
        location_dest_id INTEGER,                   -- 目标库位
        state             TEXT DEFAULT 'draft',     -- 状态
        date_expected     TEXT,                      -- 预计日期
        date_done         TEXT,
        reference         TEXT                       -- 内部参考
    )
    """)

    # ---- 3. 采购订单头（Odoo: purchase_order）----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_order (
        id             INTEGER PRIMARY KEY,
        name           TEXT UNIQUE NOT NULL,  -- PO-20250601
        partner_id     INTEGER,
        partner_name   TEXT,                   -- 供应商名称
        date_order     TEXT,                   -- 下单日期
        date_approve   TEXT,                   -- 审批日期
        invoice_status TEXT DEFAULT 'no',
        state          TEXT DEFAULT 'draft',   -- draft/purchase/done
        amount_total   REAL DEFAULT 0,
        notes          TEXT
    )
    """)

    # ---- 4. 采购订单行（Odoo: purchase_order_line）----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_order_line (
        id              INTEGER PRIMARY KEY,
        order_id        INTEGER,
        order_name      TEXT,
        product_id      INTEGER,
        product_code    TEXT,
        product_name    TEXT,
        product_uom     INTEGER,
        product_qty    REAL,
        price_unit     REAL,
        price_subtotal REAL,
        date_planned   TEXT,
        qty_received   REAL DEFAULT 0
    )
    """)

    # ---- 5. 工单（Odoo: maintenance_ticket / stock_picking）----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS maintenance_ticket (
        id           INTEGER PRIMARY KEY,
        name         TEXT UNIQUE NOT NULL,   -- TK-20250601xxx
        origin       TEXT,
        user_id      INTEGER DEFAULT 1,
        priority     TEXT DEFAULT '1',        -- 0=低，1=中，2=高，3=紧急
        categ_id     INTEGER,
        stage_id     INTEGER DEFAULT 0,
        description  TEXT,
        create_date  TEXT,
        write_date   TEXT,
        date_deadline TEXT
    )
    """)

    # ---- 检查是否已有数据 ----
    cur.execute("SELECT COUNT(*) FROM product_product")
    if cur.fetchone()[0] > 0:
        conn.close()
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d1 = (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d")
    d2 = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    d_past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

    # ============================================================
    # 种子数据
    # ============================================================

    # -- 物料（15条）--
    products = [
        # (default_code, name, categ_id, uom_id, standard_price, qty_available, virtual, incoming, outgoing)
        ("MAT-001", "电机轴承 6205-2RS",    1, 1,  15.00, 1500, 1500,    0,    0,   now, now),
        ("MAT-002", "液压油 32#",            2, 3,  28.00,   80,   80,  500,    0,   now, now),
        ("MAT-003", "不锈钢螺栓 M10×40",     1, 1,   0.85, 3000, 3000,    0,    0,   now, now),
        ("MAT-004", "传送带橡胶皮带 1200mm",  3, 4, 350.00,   15,   15,    0,   10,   now, now),
        ("MAT-005", "PLC 控制器模块 FM352",  4, 1, 1200.00,    5,    5,    0,    0,   now, now),
        ("MAT-006", "气动电磁阀 4V210-08",   2, 1,  85.00,   45,   45,  200,    0,   now, now),
        ("MAT-007", "减速机轴承 30205",      1, 1,  32.00,  320,  320,    0,    0,   now, now),
        ("MAT-008", "润滑油 46#",            2, 3,  22.00,  200,  200,    0,    0,   now, now),
        ("MAT-009", "法兰联轴器 TL5型",      3, 1, 180.00,   28,   28,    0,    0,   now, now),
        ("MAT-010", "安全继电器 G6Q-2A",     4, 1,  95.00,   60,   60,    0,    0,   now, now),
        ("MAT-011", "压力传感器 0-1MPa",     2, 1, 420.00,   12,   12,   50,    0,   now, now),
        ("MAT-012", "不锈钢管 φ25×3",        3, 5,  65.00,  180,  180,    0,    0,   now, now),
        ("MAT-013", "工业触摸屏 7寸",         4, 1, 890.00,    8,    8,    0,    0,   now, now),
        ("MAT-014", "冷却泵机械密封",         1, 1, 145.00,   22,   22,    0,    0,   now, now),
        ("MAT-015", "控制柜空气开关 63A",     4, 1,  55.00,   90,   90,    0,    0,   now, now),
    ]
    cur.executemany(
        "INSERT INTO product_product (default_code, name, categ_id, uom_id, standard_price, "
        "qty_available, virtual_available, incoming_qty, outgoing_qty, create_date, write_date) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        products
    )

    # -- 采购订单（3条）--
    orders = [
        # (name, partner_id, partner_name, date_order, date_approve, invoice_status, state, amount_total, notes)
        ("PO-20250601", 101, "东莞精密轴承有限公司",  d_past,  d_past,  "no", "purchase", 24500.00, "紧急备货"),
        ("PO-20250602", 102, "广州液压器材厂",        d1,      None,    "no", "purchase", 14000.00, "定期采购"),
        ("PO-20250603", 103, "深圳传动设备科技",        d_past,  None,    "no", "draft",    9500.00, "试样物料"),
    ]
    cur.executemany(
        "INSERT INTO purchase_order (name, partner_id, partner_name, date_order, date_approve, "
        "invoice_status, state, amount_total, notes) VALUES (?,?,?,?,?,?,?,?,?)",
        orders
    )

    # -- 采购订单行 --
    order_lines = [
        # order_id, order_name, product_id, product_code, product_name, product_uom, product_qty, price_unit, price_subtotal, date_planned, qty_received
        (1, "PO-20250601",  1, "MAT-001", "电机轴承 6205-2RS",    1,  500,  15.00,  7500.00, d1,      0),
        (1, "PO-20250601",  6, "MAT-006", "气动电磁阀 4V210-08",   1,  200,  85.00, 17000.00, d1,      0),   # 两行合计 24500
        (2, "PO-20250602",  2, "MAT-002", "液压油 32#",            3,  500,  28.00, 14000.00, d2,      0),
        (3, "PO-20250603",  4, "MAT-004", "传送带橡胶皮带 1200mm",  4,   10, 350.00,  3500.00, d2,      0),
        (3, "PO-20250603",  5, "MAT-005", "PLC 控制器模块 FM352",  1,    5, 1200.00,  6000.00, d2,      0),
    ]
    cur.executemany(
        "INSERT INTO purchase_order_line "
        "(order_id, order_name, product_id, product_code, product_name, product_uom, "
        "product_qty, price_unit, price_subtotal, date_planned, qty_received) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        order_lines
    )

    # -- 库存移动（在途入库）--
    moves = [
        # (origin, product_id, product_uom_qty, location_id, location_dest_id, state, date_expected, date_done, reference)
        ("PO-20250602",  2,  500, 8, 9, "assigned", d2,  None, "IN/2025/001"),   # MAT-002 在途
        ("PO-20250601",  6,  200, 8, 9, "assigned", d1,  None, "IN/2025/002"),   # MAT-006 在途
        ("PO-20250604", 11,   50, 8, 9, "assigned", d2,  None, "IN/2025/003"),   # MAT-011 在途
    ]
    cur.executemany(
        "INSERT INTO stock_move "
        "(origin, product_id, product_uom_qty, location_id, location_dest_id, "
        "state, date_expected, date_done, reference) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        moves
    )

    conn.commit()
    conn.close()
    print(f"[init_db] OK — {DB_PATH}")


if __name__ == "__main__":
    init_db()
