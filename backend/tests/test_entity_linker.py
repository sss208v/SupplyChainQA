# -*- coding: utf-8 -*-
"""EntityLinker 单测（P1-2）：别名词典实体链接。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.entity_linker import EntityLinker


def _write_dict(tmp_path, aliases: dict) -> str:
    p = tmp_path / "entity_aliases.json"
    p.write_text(json.dumps({"aliases": aliases}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_link_supplier_name_hit(tmp_path):
    """正常路径：查询含供应商全名，链接到 Supplier 实体。"""
    p = _write_dict(tmp_path, {
        "东莞精密轴承有限公司": {"entity": "东莞精密轴承有限公司", "type": "Supplier"},
    })
    linker = EntityLinker(p)
    got = linker.link("供应商 东莞精密轴承有限公司 供应哪些物料？")
    assert got == [{"entity": "东莞精密轴承有限公司", "type": "Supplier"}]


def test_link_material_short_alias(tmp_path):
    """物料中文短名链接到编码，最长别名优先且命中段不重叠。"""
    p = _write_dict(tmp_path, {
        "液压油": {"entity": "MAT-002", "type": "Material"},
        "液压油 32#": {"entity": "MAT-002", "type": "Material"},
    })
    linker = EntityLinker(p)
    got = linker.link("液压油 32# 的库存还有多少")
    assert got == [{"entity": "MAT-002", "type": "Material"}]  # 同实体去重，只出一次


def test_link_no_hit_returns_empty(tmp_path):
    """未命中：普通制度问答不产生任何链接。"""
    p = _write_dict(tmp_path, {
        "液压油": {"entity": "MAT-002", "type": "Material"},
    })
    linker = EntityLinker(p)
    assert linker.link("采购订单的审批路径是什么？") == []


def test_link_empty_query_and_missing_dict(tmp_path):
    """空输入与词典缺失：都返回空列表不抛错。"""
    p = _write_dict(tmp_path, {"液压油": {"entity": "MAT-002", "type": "Material"}})
    linker = EntityLinker(p)
    assert linker.link("") == []
    linker_missing = EntityLinker(str(tmp_path / "nonexistent.json"))
    assert linker_missing.link("液压油") == []


def test_link_max_entities_cap(tmp_path):
    """实体数量上限：默认最多返回 3 个。"""
    aliases = {f"物料{i}号": {"entity": f"MAT-00{i}", "type": "Material"} for i in range(1, 6)}
    p = _write_dict(tmp_path, aliases)
    linker = EntityLinker(p)
    q = "物料1号 物料2号 物料3号 物料4号 物料5号 都缺货了"
    assert len(linker.link(q)) == 3


def test_hot_reload_on_mtime_change(tmp_path):
    """mtime 热加载：词典更新后无需重启即生效。"""
    p = _write_dict(tmp_path, {"液压油": {"entity": "MAT-002", "type": "Material"}})
    linker = EntityLinker(p)
    assert linker.link("液压油缺货") != []
    time.sleep(0.01)
    os.utime(p)  # 先确保 mtime 变化可感知
    (tmp_path / "entity_aliases.json").write_text(
        json.dumps({"aliases": {"轴承": {"entity": "MAT-001", "type": "Material"}}}, ensure_ascii=False),
        encoding="utf-8")
    assert linker.link("轴承缺货") == [{"entity": "MAT-001", "type": "Material"}]
    assert linker.link("液压油缺货") == []
