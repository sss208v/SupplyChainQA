"""
SupplyChainRAG - 意图路由配置加载器
============================================================
将路由规则从代码中外置到 app/data/intent_routes.json：
- tool_commands: 精确命令词 → 工具名（规则层确定性短路）
- entity_rules:  实体编码前缀 + 领域提示词 → 工具名（实体优先路由）
- graph_keywords / goal_keywords: 图谱/目标型关键词
- rag_patterns:  高精度知识问句正则（问句形态，非领域泛词）
- semantic_routes: 各意图的代表话术（语义路由样本，支持 per-intent 阈值）

【设计说明】
- 热加载：每次 get() 检查文件 mtime（一次 os.stat，微秒级），变化即重载，
  新增工具/关键词只改配置不改代码。
- 校验：工具名必须在 TOOL_REGISTRY 中，非法条目丢弃并 logger.warning，
  防止配置错误导致路由到不存在的工具。
- 降级：文件缺失/损坏时保留上一份有效配置（或空配置），不阻断路由主链路。
============================================================
"""
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 配置文件路径（app/core/ 相对 app/data/）
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "intent_routes.json"
)

# 实体编码正则（与 graph_engine.py 的编码体系保持同步）
ENTITY_CODE_RE = re.compile(r"(MAT-\d+|PO-\d+|TK-\d+)", re.IGNORECASE)


@dataclass
class EntityRule:
    """实体编码路由规则：编码前缀 + 领域提示词 → 工具"""
    prefix: str          # 如 "MAT-"
    tool: str            # 如 "query_inventory"
    hints: list[str]     # 领域提示词，如 ["库存", "缺货"]


@dataclass
class IntentRoutesConfig:
    """一份完整的意图路由配置（不可变快照，重载时整体替换）"""
    tool_commands: dict[str, str] = field(default_factory=dict)
    entity_rules: list[EntityRule] = field(default_factory=list)
    graph_keywords: list[str] = field(default_factory=list)
    goal_keywords: list[str] = field(default_factory=list)
    rag_patterns: list[re.Pattern] = field(default_factory=list)
    # {"intent": {"threshold": float|None, "utterances": [...]}}
    semantic_routes: dict[str, dict] = field(default_factory=dict)
    version: int = 0  # 每次重载递增，供 SemanticRouter 检测样本变化


def _validated_tool_names() -> set[str]:
    """获取已注册工具名集合（延迟导入避免循环依赖）"""
    try:
        from app.core.tool_engine import TOOL_REGISTRY
        return set(TOOL_REGISTRY.keys())
    except Exception as e:
        logger.warning(f"[IntentRoutes] TOOL_REGISTRY 不可用，跳过工具名校验: {e}")
        return set()


class IntentRoutesManager:
    """配置管理器：加载 + mtime 热加载 + 校验"""

    def __init__(self, config_path: Optional[str] = None):
        self._path = os.path.abspath(config_path or _DEFAULT_CONFIG_PATH)
        self._lock = threading.Lock()
        self._config = IntentRoutesConfig()
        self._mtime: float = -1.0
        self._version: int = 0

    def get(self) -> IntentRoutesConfig:
        """获取当前配置（文件变化时自动重载）"""
        try:
            mtime = os.stat(self._path).st_mtime
        except OSError:
            mtime = -1.0

        if mtime != self._mtime:
            self.reload()
        return self._config

    def reload(self) -> IntentRoutesConfig:
        """强制重载配置文件；失败时保留上一份有效配置"""
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._version += 1
                self._config = self._parse(raw, self._version)
                self._mtime = os.stat(self._path).st_mtime
                logger.info(
                    f"[IntentRoutes] 配置加载完成 v{self._version}: "
                    f"{len(self._config.tool_commands)} 命令词 / "
                    f"{len(self._config.entity_rules)} 实体规则 / "
                    f"{sum(len(v.get('utterances', [])) for v in self._config.semantic_routes.values())} 语义样本"
                )
            except FileNotFoundError:
                logger.warning(f"[IntentRoutes] 配置文件不存在: {self._path}，使用当前配置")
                self._mtime = -1.0
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"[IntentRoutes] 配置解析失败，保留上一份有效配置: {e}")
                # 记录 mtime 避免每次请求重复解析坏文件
                try:
                    self._mtime = os.stat(self._path).st_mtime
                except OSError:
                    self._mtime = -1.0
            return self._config

    @staticmethod
    def _parse(raw: dict, version: int) -> IntentRoutesConfig:
        """解析 + 校验原始 JSON 配置"""
        valid_tools = _validated_tool_names()

        def _tool_ok(name: str) -> bool:
            # TOOL_REGISTRY 不可用时（如极简测试环境）跳过校验
            return not valid_tools or name in valid_tools

        # 1. 命令词 → 工具（非法工具名丢弃）
        tool_commands: dict[str, str] = {}
        for kw, tool in (raw.get("tool_commands") or {}).items():
            if _tool_ok(tool):
                tool_commands[kw] = tool
            else:
                logger.warning(f"[IntentRoutes] 未注册工具名被忽略: {kw} -> {tool}")

        # 2. 实体规则（非法工具名丢弃）
        entity_rules: list[EntityRule] = []
        for item in (raw.get("entity_rules") or []):
            prefix = item.get("prefix", "")
            tool = item.get("tool", "")
            if not prefix or not tool:
                continue
            if not _tool_ok(tool):
                logger.warning(f"[IntentRoutes] 实体规则工具名未注册，忽略: {prefix} -> {tool}")
                continue
            entity_rules.append(
                EntityRule(prefix=prefix.upper(), tool=tool, hints=list(item.get("hints") or []))
            )

        # 3. RAG 问句正则（非法正则跳过）
        rag_patterns: list[re.Pattern] = []
        for p in (raw.get("rag_patterns") or []):
            try:
                rag_patterns.append(re.compile(p))
            except re.error as e:
                logger.warning(f"[IntentRoutes] 非法正则被忽略: {p} ({e})")

        # 4. 语义路由样本（空 utterances 的意图丢弃）
        semantic_routes: dict[str, dict] = {}
        for intent, route in (raw.get("semantic_routes") or {}).items():
            utterances = list((route or {}).get("utterances") or [])
            if not utterances:
                logger.warning(f"[IntentRoutes] 意图 {intent} 无语义样本，忽略")
                continue
            semantic_routes[intent] = {
                "threshold": (route or {}).get("threshold"),
                "utterances": utterances,
            }

        return IntentRoutesConfig(
            tool_commands=tool_commands,
            entity_rules=entity_rules,
            graph_keywords=list(raw.get("graph_keywords") or []),
            goal_keywords=list(raw.get("goal_keywords") or []),
            rag_patterns=rag_patterns,
            semantic_routes=semantic_routes,
            version=version,
        )


# 模块级单例
_manager: Optional[IntentRoutesManager] = None


def get_intent_routes_manager() -> IntentRoutesManager:
    global _manager
    if _manager is None:
        _manager = IntentRoutesManager()
    return _manager


def get_intent_routes() -> IntentRoutesConfig:
    """获取当前意图路由配置（自动热加载）"""
    return get_intent_routes_manager().get()
