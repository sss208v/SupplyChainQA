"""三层记忆体系单元测试（用户画像 / 部门记忆 / 企业术语表）

覆盖 AGENTS.md 测试规范要求的四类场景：
- 正常路径：写入→读取→上下文拼装
- 空输入：空串/空白跳过
- 权限不足：跨部门读写、非 admin 维护术语表
- 并发：asyncio.gather 并发写入不抛错

全部使用内存 fake Redis，不依赖 Docker。
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.core.redis_client as rc_mod
from app.core.memory_service import (
    extract_profile_signals,
    get_memory_service,
    reset_memory_service,
)

# ============================================================
# 内存版 fake Redis（string + hash 操作，pipeline 真实执行）
# ============================================================

class _MemPipeline:
    def __init__(self, fake):
        self._fake = fake
        self._ops = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return _record

    async def execute(self):
        results = []
        for name, args, kwargs in self._ops:
            if name == "hset":
                results.append(await self._fake.hset(*args))
            else:
                results.append(True)
        self._ops = []
        return results


class _MemFakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}
        self._hashes: dict[str, dict] = {}

    async def hset(self, key, field, value):
        self._hashes.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        return self._hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return dict(self._hashes.get(key, {}))

    async def hdel(self, key, *fields):
        h = self._hashes.get(key, {})
        removed = 0
        for f in fields:
            if h.pop(f, None) is not None:
                removed += 1
        return removed

    async def hlen(self, key):
        return len(self._hashes.get(key, {}))

    async def hexists(self, key, field):
        return field in self._hashes.get(key, {})

    async def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._hashes.pop(k, None)
        return len(keys)

    def pipeline(self, transaction=True):
        return _MemPipeline(self)


class _MemFakeManager:
    def __init__(self, connected=True):
        self._pool = _MemFakeRedis()
        self._connected = connected

    @property
    def client(self):
        return self._pool

    @property
    def is_connected(self):
        return self._connected

    async def ensure_connected(self):
        return self._connected


# 默认设置快照（与 config 默认值一致的 SimpleNamespace，便于 monkeypatch 单项）
def _default_settings():
    return SimpleNamespace(
        MEMORY_INJECT_ENABLED=True,
        PROFILE_MAX_ITEMS=20,
        PROFILE_TTL=2592000,
        DEPT_MEMORY_MAX=50,
        DEPT_MEMORY_TTL=2592000,
        GLOSSARY_MAX_TERMS=200,
    )


def _make_service(monkeypatch, connected=True, **settings_overrides):
    """构造绑定 fake manager 的 MemoryService，并 patch 模块级 settings"""
    import app.core.memory_service as mem_mod

    fake_settings = _default_settings()
    for k, v in settings_overrides.items():
        setattr(fake_settings, k, v)
    monkeypatch.setattr(mem_mod, "settings", fake_settings)

    manager = _MemFakeManager(connected=connected)
    monkeypatch.setattr(rc_mod, "redis_manager", manager)
    reset_memory_service()
    return get_memory_service(), manager


# ============================================================
# 用户层：UserProfileStore
# ============================================================

class TestUserProfileStore:
    async def test_add_and_get_preference(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.profile.add_preference("alice", "偏好简洁回答")
        profile = await svc.profile.get_profile("alice")
        assert profile["preferences"] == ["偏好简洁回答"]
        ctx = await svc.profile.get_profile_context("alice")
        assert "【用户背景】" in ctx
        assert "偏好简洁回答" in ctx

    async def test_deduplicate_same_content(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.profile.add_preference("alice", "偏好简洁回答")
        await svc.profile.add_preference("alice", "偏好简洁回答")
        profile = await svc.profile.get_profile("alice")
        assert len(profile["preferences"]) == 1

    async def test_empty_input_skipped(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.profile.add_preference("alice", "")
        await svc.profile.add_preference("alice", "   ")
        profile = await svc.profile.get_profile("alice")
        assert profile["preferences"] == []

    async def test_cap_items_keeps_latest(self, monkeypatch):
        svc, _ = _make_service(monkeypatch, PROFILE_MAX_ITEMS=3)
        for i in range(5):
            await svc.profile.add_preference("alice", f"偏好{i}")
        profile = await svc.profile.get_profile("alice")
        assert len(profile["preferences"]) == 3
        assert profile["preferences"] == ["偏好2", "偏好3", "偏好4"]

    async def test_redis_unavailable_graceful(self, monkeypatch):
        svc, _ = _make_service(monkeypatch, connected=False)
        await svc.profile.add_preference("alice", "偏好简洁回答")  # 不抛错
        profile = await svc.profile.get_profile("alice")
        assert profile["preferences"] == []

    async def test_concurrent_writes(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await asyncio.gather(*[
            svc.profile.add_topic("alice", f"主题{i}") for i in range(10)
        ])
        profile = await svc.profile.get_profile("alice")
        assert len(profile["topics"]) == 10  # 无异常、无覆盖丢失

    async def test_clear_profile(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.profile.add_preference("alice", "偏好简洁回答")
        await svc.profile.clear_profile("alice")
        profile = await svc.profile.get_profile("alice")
        assert profile["preferences"] == []

    async def test_terms_and_topics_roundtrip(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.profile.add_term("alice", "物料编码")
        await svc.profile.add_topic("alice", "库存周转率")
        ctx = await svc.profile.get_profile_context("alice")
        assert "物料编码" in ctx
        assert "库存周转率" in ctx


# ============================================================
# 部门层：DeptMemoryStore（角色校验）
# ============================================================

class TestDeptMemoryStore:
    async def test_add_and_get_own_dept(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.dept.add_note("purchase", "供应商准入需双人复核", "bob", "purchase")
        notes = await svc.dept.get_notes("purchase", "purchase")
        assert len(notes) == 1
        assert notes[0]["content"] == "供应商准入需双人复核"
        ctx = await svc.dept.get_dept_context("purchase", "purchase")
        assert "【部门记忆】" in ctx

    async def test_cross_dept_read_forbidden(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        with pytest.raises(PermissionError):
            await svc.dept.get_notes("warehouse", "purchase")

    async def test_cross_dept_write_forbidden(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        with pytest.raises(PermissionError):
            await svc.dept.add_note("warehouse", "越权写入", "bob", "purchase")

    async def test_admin_cross_dept_allowed(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.dept.add_note("warehouse", "盘点规则更新", "admin", "admin")
        notes = await svc.dept.get_notes("warehouse", "admin")
        assert len(notes) == 1

    async def test_invalid_dept_role_forbidden(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        with pytest.raises(PermissionError):
            await svc.dept.add_note("employee", "非法部门", "bob", "purchase")

    async def test_empty_content_skipped(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.dept.add_note("purchase", "  ", "bob", "purchase")
        notes = await svc.dept.get_notes("purchase", "purchase")
        assert notes == []

    async def test_deduplicate_same_note(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.dept.add_note("purchase", "审批时效24小时", "bob", "purchase")
        await svc.dept.add_note("purchase", "审批时效24小时", "bob", "purchase")
        notes = await svc.dept.get_notes("purchase", "purchase")
        assert len(notes) == 1

    async def test_clear_dept(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.dept.add_note("purchase", "审批时效24小时", "bob", "purchase")
        await svc.dept.clear_dept("purchase", "purchase")
        assert await svc.dept.get_notes("purchase", "purchase") == []


# ============================================================
# 企业层：GlossaryStore（admin 权限）
# ============================================================

class TestGlossaryStore:
    async def test_admin_add_and_get(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.glossary.add_term("SKU", "库存量单位", "admin")
        terms = await svc.glossary.get_terms()
        assert terms == {"SKU": "库存量单位"}
        ctx = await svc.glossary.get_glossary_context()
        assert "【企业术语表】" in ctx
        assert "SKU" in ctx

    async def test_non_admin_write_forbidden(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        with pytest.raises(PermissionError):
            await svc.glossary.add_term("SKU", "库存量单位", "purchase")

    async def test_admin_delete(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.glossary.add_term("SKU", "库存量单位", "admin")
        await svc.glossary.delete_term("SKU", "admin")
        assert await svc.glossary.get_terms() == {}

    async def test_non_admin_delete_forbidden(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.glossary.add_term("SKU", "库存量单位", "admin")
        with pytest.raises(PermissionError):
            await svc.glossary.delete_term("SKU", "purchase")

    async def test_cap_limit_rejected(self, monkeypatch):
        svc, _ = _make_service(monkeypatch, GLOSSARY_MAX_TERMS=2)
        await svc.glossary.add_term("A", "定义A", "admin")
        await svc.glossary.add_term("B", "定义B", "admin")
        await svc.glossary.add_term("C", "定义C", "admin")  # 超限拒绝
        terms = await svc.glossary.get_terms()
        assert "C" not in terms

    async def test_redis_unavailable_graceful(self, monkeypatch):
        svc, _ = _make_service(monkeypatch, connected=False)
        await svc.glossary.add_term("SKU", "库存量单位", "admin")  # 不抛错
        assert await svc.glossary.get_terms() == {}


# ============================================================
# MemoryService.build_memory_context（三层拼装）
# ============================================================

class TestBuildMemoryContext:
    async def test_three_layer_injection(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        # 用户层
        await svc.profile.add_preference("alice", "偏好简洁回答")
        # 部门层
        await svc.dept.add_note("purchase", "审批时效24小时", "bob", "purchase")
        # 企业层
        await svc.glossary.add_term("SKU", "库存量单位", "admin")

        ctx = await svc.build_memory_context(user_id="alice", user_role="purchase")
        assert "【用户背景】" in ctx
        assert "【部门记忆】" in ctx
        assert "【企业术语表】" in ctx

    async def test_inject_disabled_returns_empty(self, monkeypatch):
        svc, _ = _make_service(monkeypatch, MEMORY_INJECT_ENABLED=False)
        await svc.profile.add_preference("alice", "偏好简洁回答")
        ctx = await svc.build_memory_context(user_id="alice", user_role="purchase")
        assert ctx == ""

    async def test_no_user_id_only_glossary(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        await svc.glossary.add_term("SKU", "库存量单位", "admin")
        ctx = await svc.build_memory_context()
        assert "【企业术语表】" in ctx
        assert "【用户背景】" not in ctx
        assert "【部门记忆】" not in ctx

    async def test_admin_no_dept_section(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        ctx = await svc.build_memory_context(user_id="root", user_role="admin")
        assert "【部门记忆】" not in ctx

    async def test_all_empty_returns_empty(self, monkeypatch):
        svc, _ = _make_service(monkeypatch)
        ctx = await svc.build_memory_context(user_id="alice", user_role="purchase")
        assert ctx == ""


# ============================================================
# extract_profile_signals（轻量规则提炼）
# ============================================================

class TestExtractProfileSignals:
    def test_preference_pattern(self):
        signals = extract_profile_signals("我喜欢简洁的回答方式，帮我查一下库存")
        assert any("简洁的回答方式" in p for p in signals["preferences"])

    def test_domain_term_hit(self):
        signals = extract_profile_signals("采购订单的审批流程是怎样的")
        assert "采购" in signals["terms"] or "审批" in signals["terms"]

    def test_empty_input(self):
        assert extract_profile_signals("") == {"preferences": [], "terms": [], "topics": []}
        assert extract_profile_signals(None) == {"preferences": [], "terms": [], "topics": []}

    def test_topic_extraction(self):
        signals = extract_profile_signals("帮我分析库存周转率偏低的原因")
        assert signals["topics"]  # 主题非空


# ============================================================
# 单例管理
# ============================================================

class TestSingleton:
    def test_get_memory_service_rebinds_after_reset(self, monkeypatch):
        manager = _MemFakeManager()
        monkeypatch.setattr(rc_mod, "redis_manager", manager)
        reset_memory_service()
        svc1 = get_memory_service()
        assert svc1 is not None
        reset_memory_service()
        svc2 = get_memory_service()
        assert svc2 is not None

    def test_get_memory_service_none_when_manager_missing(self, monkeypatch):
        monkeypatch.setattr(rc_mod, "redis_manager", None)
        reset_memory_service()
        assert get_memory_service() is None
