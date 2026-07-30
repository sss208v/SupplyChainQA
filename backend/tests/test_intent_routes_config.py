"""
tests/test_intent_routes_config.py — 意图路由配置加载器单元测试

覆盖范围：
  1. 真实配置文件加载：结构完整、工具名全部合法
  2. 校验：非法工具名拒绝 / 非法正则跳过 / 空 utterances 意图丢弃
  3. 热加载：文件 mtime 变化触发重载、version 递增
  4. 降级：文件缺失 / JSON 损坏时不抛异常且保留上一份配置
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.intent_routes import (
    IntentRoutesManager,
    get_intent_routes,
    ENTITY_CODE_RE,
)
from app.core.tool_engine import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# 1. 真实配置文件
# ---------------------------------------------------------------------------

class TestRealConfig:
    """加载仓库内真实的 intent_routes.json。"""

    def test_config_loads_with_content(self):
        cfg = get_intent_routes()
        assert cfg.tool_commands, "命令词表不应为空"
        assert cfg.entity_rules, "实体规则不应为空"
        assert cfg.graph_keywords, "图谱关键词不应为空"
        assert cfg.goal_keywords, "目标关键词不应为空"
        assert cfg.rag_patterns, "RAG 问句正则不应为空"
        assert cfg.semantic_routes, "语义路由样本不应为空"

    def test_all_tool_names_registered(self):
        """配置中的工具名必须全部在 TOOL_REGISTRY 中（防幻觉配置）"""
        cfg = get_intent_routes()
        for kw, tool in cfg.tool_commands.items():
            assert tool in TOOL_REGISTRY, f"命令词 {kw} 指向未注册工具 {tool}"
        for rule in cfg.entity_rules:
            assert rule.tool in TOOL_REGISTRY, f"实体规则 {rule.prefix} 指向未注册工具 {rule.tool}"

    def test_semantic_routes_cover_core_intents(self):
        """语义路由样本覆盖 5 个核心意图（含新增的 goal / graph_query）"""
        cfg = get_intent_routes()
        for intent in ("rag_answer", "tool_call", "greeting", "goal", "graph_query"):
            assert intent in cfg.semantic_routes, f"缺少意图 {intent} 的语义样本"
            assert len(cfg.semantic_routes[intent]["utterances"]) >= 3

    def test_entity_code_regex(self):
        assert ENTITY_CODE_RE.search("查一下MAT-001的库存")
        assert ENTITY_CODE_RE.search("po-20250101到货了吗")
        assert ENTITY_CODE_RE.search("追溯TK-100")
        assert ENTITY_CODE_RE.search("SUP-001供应商") is None


# ---------------------------------------------------------------------------
# 2. 校验逻辑
# ---------------------------------------------------------------------------

def _write_config(path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class TestValidation:
    """配置校验：非法条目丢弃而不是加载失败。"""

    def test_invalid_tool_name_rejected(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {
            "tool_commands": {"查库存": "query_inventory", "查成本": "fake_tool"},
            "entity_rules": [
                {"prefix": "MAT-", "tool": "query_inventory", "hints": ["库存"]},
                {"prefix": "PO-", "tool": "nonexistent_tool", "hints": ["订单"]},
            ],
        })
        cfg = IntentRoutesManager(str(path)).get()
        assert cfg.tool_commands == {"查库存": "query_inventory"}
        assert len(cfg.entity_rules) == 1
        assert cfg.entity_rules[0].tool == "query_inventory"

    def test_invalid_regex_skipped(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {
            "rag_patterns": ["^什么是", "([invalid"],
        })
        cfg = IntentRoutesManager(str(path)).get()
        assert len(cfg.rag_patterns) == 1
        assert cfg.rag_patterns[0].search("什么是安全库存")

    def test_empty_utterances_intent_dropped(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {
            "semantic_routes": {
                "rag_answer": {"threshold": None, "utterances": ["什么是安全库存"]},
                "tool_call": {"threshold": None, "utterances": []},
            },
        })
        cfg = IntentRoutesManager(str(path)).get()
        assert "rag_answer" in cfg.semantic_routes
        assert "tool_call" not in cfg.semantic_routes

    def test_per_intent_threshold_parsed(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {
            "semantic_routes": {
                "greeting": {"threshold": 0.8, "utterances": ["你好"]},
            },
        })
        cfg = IntentRoutesManager(str(path)).get()
        assert cfg.semantic_routes["greeting"]["threshold"] == 0.8


# ---------------------------------------------------------------------------
# 3. 热加载
# ---------------------------------------------------------------------------

class TestHotReload:
    """mtime 变化触发重载，version 递增。"""

    def test_mtime_change_triggers_reload(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {"goal_keywords": ["帮我评估"]})

        manager = IntentRoutesManager(str(path))
        cfg1 = manager.get()
        assert cfg1.goal_keywords == ["帮我评估"]
        v1 = cfg1.version

        # 修改文件（确保 mtime 变化）
        time.sleep(0.01)
        _write_config(path, {"goal_keywords": ["帮我评估", "帮我分析"]})
        os.utime(path)

        cfg2 = manager.get()
        assert cfg2.goal_keywords == ["帮我评估", "帮我分析"]
        assert cfg2.version > v1

    def test_no_change_no_reload(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {"goal_keywords": ["帮我评估"]})

        manager = IntentRoutesManager(str(path))
        cfg1 = manager.get()
        cfg2 = manager.get()
        assert cfg1 is cfg2  # 未变化时返回同一份快照


# ---------------------------------------------------------------------------
# 4. 降级行为
# ---------------------------------------------------------------------------

class TestDegradation:
    """文件缺失 / 损坏时不抛异常。"""

    def test_missing_file_returns_empty_config(self, tmp_path):
        manager = IntentRoutesManager(str(tmp_path / "nonexistent.json"))
        cfg = manager.get()
        assert cfg.tool_commands == {}
        assert cfg.semantic_routes == {}

    def test_corrupt_json_keeps_previous_config(self, tmp_path):
        path = tmp_path / "routes.json"
        _write_config(path, {"goal_keywords": ["帮我评估"]})

        manager = IntentRoutesManager(str(path))
        cfg1 = manager.get()
        assert cfg1.goal_keywords == ["帮我评估"]

        # 写坏文件
        time.sleep(0.01)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not valid json {{{")
        os.utime(path)

        cfg2 = manager.get()
        # 保留上一份有效配置
        assert cfg2.goal_keywords == ["帮我评估"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
