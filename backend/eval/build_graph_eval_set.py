# -*- coding: utf-8 -*-
"""从 Neo4j 图谱反向生成图谱触发评测子集（P1-1）。

与 knowledge/*.md 制度问答不同，本子集的事实来自 SQLite→Neo4j 的业务图数据
（物料/供应商/采购订单关系），仅图谱检索路能召回——是图谱能力的专用测量仪。

reference 直接由图数据真值生成（措辞对齐 get_2hop_subgraph_context 的表述），
天然 100% grounded，无需 LLM 核验；仍建议用 make_review_checklist 人工过目。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\build_graph_eval_set.py
产出：
  eval/eval_set_graph.json  [{question, reference_answer, source_file, type}]
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.neo4j_client import neo4j_client

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(EVAL_DIR, "eval_set_graph.json")
MAX_QUESTIONS = 30


async def _fetch_graph_facts() -> dict:
    """拉取三类实体的关系真值（与 get_2hop_subgraph_context 同源 Cypher）。"""
    facts = {"materials": [], "orders": [], "suppliers": []}
    async with neo4j_client.get_session() as session:
        # 物料 → 供应商/订单
        result = await session.run("""
            MATCH (m:Material)
            OPTIONAL MATCH (ol:OrderLine)-[:FOR]->(m)
            OPTIONAL MATCH (po:PurchaseOrder)-[:CONTAINS]->(ol)
            OPTIONAL MATCH (po)-[:FROM]->(s:Supplier)
            RETURN m.code AS code, m.name AS name,
                   collect(DISTINCT s.name) AS suppliers,
                   collect(DISTINCT po.code) AS orders
            ORDER BY code
        """)
        async for r in result:
            facts["materials"].append({
                "code": r["code"], "name": r["name"],
                "suppliers": [x for x in r["suppliers"] if x],
                "orders": [x for x in r["orders"] if x],
            })

        # 订单 → 供应商/物料
        result = await session.run("""
            MATCH (po:PurchaseOrder)
            OPTIONAL MATCH (po)-[:FROM]->(s:Supplier)
            OPTIONAL MATCH (po)-[:CONTAINS]->(ol:OrderLine)-[:FOR]->(m:Material)
            RETURN po.code AS code, po.state AS state,
                   collect(DISTINCT s.name) AS suppliers,
                   collect(DISTINCT m.code) AS materials
            ORDER BY code
        """)
        async for r in result:
            facts["orders"].append({
                "code": r["code"], "state": r["state"],
                "suppliers": [x for x in r["suppliers"] if x],
                "materials": [x for x in r["materials"] if x],
            })

        # 供应商 → 订单/物料
        result = await session.run("""
            MATCH (s:Supplier)
            OPTIONAL MATCH (po:PurchaseOrder)-[:FROM]->(s)
            OPTIONAL MATCH (po)-[:CONTAINS]->(ol:OrderLine)-[:FOR]->(m:Material)
            RETURN s.name AS name,
                   collect(DISTINCT po.code) AS orders,
                   collect(DISTINCT m.code) AS materials
            ORDER BY name
        """)
        async for r in result:
            facts["suppliers"].append({
                "name": r["name"],
                "orders": [x for x in r["orders"] if x],
                "materials": [x for x in r["materials"] if x],
            })
    return facts


def _build_questions(facts: dict) -> list[dict]:
    """按真值生成 QA（措辞对齐 get_2hop_subgraph_context 的声明式表述）。"""
    items = []

    for m in facts["materials"]:
        if m["suppliers"]:
            items.append({
                "question": f"物料 {m['code']} 由哪个供应商供应？",
                "reference_answer": f"物料 {m['code']}（{m['name']}）由供应商 {'、'.join(m['suppliers'])} 供应。",
                "source_file": "neo4j_graph",
                "type": "graph",
            })
        if m["orders"]:
            items.append({
                "question": f"物料 {m['code']} 关联哪些采购订单？",
                "reference_answer": f"物料 {m['code']} 关联采购订单 {'、'.join(sorted(m['orders']))}。",
                "source_file": "neo4j_graph",
                "type": "graph",
            })

    for po in facts["orders"]:
        if po["suppliers"] and po["materials"]:
            items.append({
                "question": f"采购订单 {po['code']} 来自哪个供应商？包含哪些物料？",
                "reference_answer": (
                    f"采购订单 {po['code']} 来自供应商 {'、'.join(po['suppliers'])}，"
                    f"包含物料 {'、'.join(sorted(po['materials']))}。"
                ),
                "source_file": "neo4j_graph",
                "type": "graph",
            })

    for s in facts["suppliers"]:
        if s["orders"]:
            items.append({
                "question": f"供应商 {s['name']} 有哪些采购订单？",
                "reference_answer": f"供应商 {s['name']} 有采购订单 {'、'.join(sorted(s['orders']))}。",
                "source_file": "neo4j_graph",
                "type": "graph",
            })
        if s["materials"]:
            items.append({
                "question": f"供应商 {s['name']} 供应哪些物料？",
                "reference_answer": f"供应商 {s['name']} 供应物料 {'、'.join(sorted(s['materials']))}。",
                "source_file": "neo4j_graph",
                "type": "graph",
            })

    return items[:MAX_QUESTIONS]


async def main():
    ok = await neo4j_client.connect()
    if not ok:
        raise SystemExit("Neo4j 连接失败（检查 backend/.env NEO4J_URI 与容器状态）")
    try:
        facts = await _fetch_graph_facts()
        print(f"图谱实体: materials={len(facts['materials'])} "
              f"orders={len(facts['orders'])} suppliers={len(facts['suppliers'])}")
        items = _build_questions(facts)
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"生成 {len(items)} 题 -> {OUT_PATH}")
        print("[NOTE] reference 由图数据真值直接生成；建议人工过目后再作为正式子集。")
    finally:
        await neo4j_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
