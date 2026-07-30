"""
实体链接器 — 自然语言实体 → 图谱查询键（P1-2）

图谱路的实体提取原本只认编码正则（MAT-/PO-/SUP-），自然语言实体
（如"东莞精密轴承有限公司"、"液压油"）无法触发图谱检索。
本模块从 app/data/entity_aliases.json 加载 别名 → 图谱实体 词典，
对查询做最长优先的子串匹配，返回可直接用于 Neo4j 查询的实体列表。

词典外置 + mtime 热加载（与 intent_routes.json 同模式）；
词典由 scripts/build_entity_aliases.py 从 Neo4j 节点属性生成，
歧义别名（同名指向不同实体）在生成阶段整体剔除，防误链接引噪声。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "entity_aliases.json")


class EntityLinker:
    """别名词典实体链接：最长优先子串匹配，命中片段不重叠"""

    def __init__(self, data_path: str = _DATA_PATH):
        self._data_path = data_path
        self._mtime: float = -1.0
        # [(alias, entity, type)]，按别名长度降序（长别名优先，防"液压"抢"液压油"）
        self._aliases: list[tuple[str, str, str]] = []

    def _reload_if_changed(self):
        try:
            mtime = os.path.getmtime(self._data_path)
        except OSError:
            if self._mtime != -1.0 or self._aliases:
                logger.warning("[EntityLinker] 词典文件缺失: %s", self._data_path)
            self._mtime = -1.0
            self._aliases = []
            return
        if mtime == self._mtime:
            return
        try:
            with open(self._data_path, encoding="utf-8") as f:
                raw = json.load(f).get("aliases", {})
            self._aliases = sorted(
                ((a, v["entity"], v.get("type", "Material")) for a, v in raw.items()),
                key=lambda x: len(x[0]), reverse=True,
            )
            self._mtime = mtime
            logger.info("[EntityLinker] 词典加载: %d 条别名", len(self._aliases))
        except Exception as e:
            logger.warning("[EntityLinker] 词典加载失败: %s: %s", type(e).__name__, e)
            self._aliases = []

    def link(self, query: str, max_entities: int = 3) -> list[dict]:
        """返回查询中命中的图谱实体 [{entity, type}]（同实体去重，命中段不重叠）"""
        self._reload_if_changed()
        if not query or not self._aliases:
            return []
        remaining = query
        linked: list[dict] = []
        seen: set[str] = set()
        for alias, entity, etype in self._aliases:
            if len(linked) >= max_entities:
                break
            if alias in remaining and entity not in seen:
                linked.append({"entity": entity, "type": etype})
                seen.add(entity)
                # 消费命中片段，防止其子串别名重复命中同一段文本
                remaining = remaining.replace(alias, " ", 1)
        return linked


# 全局单例
entity_linker = EntityLinker()
