# -*- coding: utf-8 -*-
"""从 Neo4j 节点属性构建实体链接词典 entity_aliases.json（P1-2）。

目标：让图谱路从"只认 MAT-/PO-/SUP- 编码正则"扩展到自然语言实体
（供应商中文名、物料中文名），查询分词后先查词典再进图谱。

产出 backend/app/data/entity_aliases.json（配置外置 + mtime 热加载，
与 intent_routes.json 同模式），结构：
  {"aliases": {"别名": {"entity": "图谱查询键", "type": "Supplier|Material|PurchaseOrder"}}}

用法：
  cd backend
  venv\\Scripts\\python.exe scripts\\build_entity_aliases.py
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.neo4j_client import neo4j_client

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "data", "entity_aliases.json")


def _short_name(name: str) -> str:
    """生成短别名：物料名去型号尾巴（首个空格前），供应商名去公司后缀。"""
    short = name.split(" ")[0].split("　")[0]
    short = re.sub(r"(有限公司|股份有限公司|科技|材厂|厂)$", "", short)
    return short.strip()


async def main():
    ok = await neo4j_client.connect()
    if not ok:
        raise SystemExit("Neo4j 连接失败")
    aliases: dict[str, dict] = {}
    conflicts: set[str] = set()

    def _add(alias: str, entity: str, etype: str):
        alias = alias.strip()
        if len(alias) < 2:
            return
        if alias in aliases and aliases[alias]["entity"] != entity:
            conflicts.add(alias)  # 歧义别名整体剔除，防误链接引噪声
            return
        aliases[alias] = {"entity": entity, "type": etype}

    async with neo4j_client.get_session() as session:
        result = await session.run("MATCH (s:Supplier) RETURN s.name AS name")
        async for r in result:
            name = r["name"]
            if not name:
                continue
            _add(name, name, "Supplier")           # 图谱 Supplier 按 name 匹配
            _add(_short_name(name), name, "Supplier")

        result = await session.run("MATCH (m:Material) RETURN m.code AS code, m.name AS name")
        async for r in result:
            code, name = r["code"], r["name"]
            if not (code and name):
                continue
            _add(name, code, "Material")           # 图谱 Material 按 code 匹配
            _add(_short_name(name), code, "Material")

    await neo4j_client.disconnect()

    for c in conflicts:
        aliases.pop(c, None)

    out = {"aliases": aliases}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"生成 {len(aliases)} 条别名（剔除歧义 {len(conflicts)} 条）-> {os.path.abspath(OUT_PATH)}")


if __name__ == "__main__":
    asyncio.run(main())
