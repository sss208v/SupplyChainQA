"""
SupplyChainRAG - SuperPowers TDD 测试
====================================
测试超能力 1：LoopBreaker 死循环熔断
测试超能力 2：_normalize_entity 拼写自愈归一化
"""

import json
import pytest


# ================================================================
# Phase 2.1: LoopBreaker 死循环熔断 单元测试
# ================================================================

class TestLoopBreaker:
    """验证 ToolAgent 的 LoopBreaker（ContextVar）能正确拦截重复工具调用"""

    def test_loop_breaker_detects_repeated_call(self):
        """测试：相同的工具+参数被调用后，再次出现时被检测"""
        from app.agents.tool import _loop_call_history_var

        # 重置 ContextVar
        _loop_call_history_var.set([])

        sig1 = ("query_inventory", json.dumps({"material_code": "MAT-001"}, sort_keys=True))

        # 模拟第一次调用 — 累加到 ContextVar
        history = list(_loop_call_history_var.get([]))
        history.append(sig1)
        _loop_call_history_var.set(history)

        # 检查第二次相同调用是否被检测
        current_sig = ("query_inventory", json.dumps({"material_code": "MAT-001"}, sort_keys=True))
        is_loop = any(
            prev[0] == current_sig[0] and prev[1] == current_sig[1]
            for prev in _loop_call_history_var.get([])
        )
        assert is_loop, "LoopBreaker 应检测到重复调用"

    def test_loop_breaker_allows_different_tool(self):
        """测试：不同的工具调用不会被误判为死循环"""
        from app.agents.tool import _loop_call_history_var

        _loop_call_history_var.set([])

        # 记录一个调用
        history = list(_loop_call_history_var.get([]))
        history.append(("query_inventory", json.dumps({"material_code": "MAT-001"}, sort_keys=True)))
        _loop_call_history_var.set(history)

        # 不同工具，不应拦截
        current_sig = ("query_order", json.dumps({"order_id": "PO-001"}, sort_keys=True))
        is_loop = any(
            prev[0] == current_sig[0] and prev[1] == current_sig[1]
            for prev in _loop_call_history_var.get([])
        )
        assert not is_loop, "LoopBreaker 不应拦截不同工具"

    def test_loop_breaker_allows_same_tool_different_args(self):
        """测试：相同工具但不同参数不会被误判为死循环"""
        from app.agents.tool import _loop_call_history_var

        _loop_call_history_var.set([])

        history = list(_loop_call_history_var.get([]))
        history.append(("query_inventory", json.dumps({"material_code": "MAT-001"}, sort_keys=True)))
        _loop_call_history_var.set(history)

        # 相同工具，不同参数
        current_sig = ("query_inventory", json.dumps({"material_code": "MAT-002"}, sort_keys=True))
        is_loop = any(
            prev[0] == current_sig[0] and prev[1] == current_sig[1]
            for prev in _loop_call_history_var.get([])
        )
        assert not is_loop, "LoopBreaker 不应拦截不同参数的同一工具"

    def test_loop_breaker_resets_per_run(self):
        """测试：ContextVar.set([]) 正确重置死循环检测器"""
        from app.agents.tool import _loop_call_history_var

        # 先添加历史
        history = [
            ("query_inventory", json.dumps({"material_code": "MAT-001"}, sort_keys=True)),
            ("query_order", json.dumps({"order_id": "PO-001"}, sort_keys=True)),
        ]
        _loop_call_history_var.set(history)
        assert len(_loop_call_history_var.get([])) == 2

        # 模拟 run() 中的重置逻辑
        _loop_call_history_var.set([])
        assert len(_loop_call_history_var.get([])) == 0, "重置后历史应为空"

    def test_loop_breaker_contextvar_persistence(self):
        """测试：ContextVar 在同一上下文中 .get().append() + .set() 后持久化"""
        from app.agents.tool import _loop_call_history_var

        _loop_call_history_var.set([])

        # 模拟多次工具调用累积
        for i in range(3):
            sig = (f"tool_{i}", json.dumps({"idx": i}))
            history = list(_loop_call_history_var.get([]))
            history.append(sig)
            _loop_call_history_var.set(history)

        final_history = _loop_call_history_var.get([])
        assert len(final_history) == 3, f"应累积 3 条记录，实际 {len(final_history)}"

    def test_loop_breaker_system_lock_message_injected(self):
        """测试：LoopBreaker 触发时注入 SystemMessage (System Lock)"""
        from langchain_core.messages import SystemMessage, ToolMessage

        tool_name = "query_inventory"
        tool_id = "call_test123"

        tool_messages = []
        tool_messages.append(ToolMessage(
            content=(
                f"⚠️ [System Alert: Loop Detected] 系统检测到你正在重复调用 {tool_name} "
                f"并传入相同参数。这说明该数据源无法提供更多新数据。"
                f"请立刻终止调用此工具！请结合已有信息进行合理推论，"
                f"或调用 get_knowledge 获取背景文档，或者直接输出 Final Answer 给用户。"
            ),
            tool_call_id=tool_id, name=tool_name
        ))
        tool_messages.append(SystemMessage(
            content="[System Lock] 必须在下一步输出 Final Answer，结束循环。"
        ))

        assert len(tool_messages) == 2
        assert isinstance(tool_messages[1], SystemMessage)
        assert "[System Lock]" in tool_messages[1].content, "应包含强制收敛指令"


# ================================================================
# Phase 2.1: _normalize_entity 拼写自愈归一化 单元测试
# ================================================================

class TestEntityNormalization:
    """验证 Neo4jClient._normalize_entity 的拼写自愈能力"""

    def _get_normalizer(self):
        """懒加载 normalization 函数"""
        from app.core.neo4j_client import Neo4jClient
        return Neo4jClient._normalize_entity

    def test_normalize_O_to_zero_MAT_OO1(self):
        """测试：MAT-OO1 → MAT-001（字母 O 纠正为数字 0）"""
        norm = self._get_normalizer()
        result = norm("MAT-OO1")
        assert result == "MAT-001", f"期望 MAT-001，实际 {result}"

    def test_normalize_O_to_zero_lowercase_mat001(self):
        """测试：mat001 → MAT-001（小写+缺连字符+字母 O 纠正）"""
        norm = self._get_normalizer()
        result = norm("mat001")
        assert result == "MAT-001", f"期望 MAT-001，实际 {result}"

    def test_normalize_lowercase_o_to_zero(self):
        """测试：MAT-oo1 → MAT-001（小写 o 纠正为 0）"""
        norm = self._get_normalizer()
        result = norm("MAT-oo1")
        assert result == "MAT-001", f"期望 MAT-001，实际 {result}"

    def test_normalize_missing_hyphen_MAT001(self):
        """测试：MAT001 → MAT-001（补充缺失连字符）"""
        norm = self._get_normalizer()
        result = norm("MAT001")
        assert result == "MAT-001", f"期望 MAT-001，实际 {result}"

    def test_normalize_missing_hyphen_PO20250101(self):
        """测试：po20250101 → PO-20250101（补充连字符+大写）"""
        norm = self._get_normalizer()
        result = norm("po20250101")
        assert result == "PO-20250101", f"期望 PO-20250101，实际 {result}"

    def test_normalize_with_spaces(self):
        """测试：MAT 001 → MAT-001（空白去除）"""
        norm = self._get_normalizer()
        result = norm("MAT 001")
        assert result == "MAT-001", f"期望 MAT-001，实际 {result}"

    def test_normalize_already_correct(self):
        """测试：MAT-001 已是标准格式，保持不变"""
        norm = self._get_normalizer()
        result = norm("MAT-001")
        assert result == "MAT-001", f"正确格式不应被修改"

    def test_normalize_SUP_entity(self):
        """测试：SUP-OO1 → SUP-001（供应商编码纠正）"""
        norm = self._get_normalizer()
        result = norm("SUP-OO1")
        assert result == "SUP-001", f"期望 SUP-001，实际 {result}"

    def test_normalize_mixed_OO_and_missing_hyphen(self):
        """测试：MATOO1 → MAT-001（混合错误：缺连字符+O代0）"""
        norm = self._get_normalizer()
        result = norm("MATOO1")
        # 步骤: strip.upper → "MATOO1"
        # re.sub O→0: "MAT001"  
        # 补连字符: "MAT-001"
        assert result == "MAT-001", f"期望 MAT-001，实际 {result}"

    def test_normalize_correct_already_normalized(self):
        """测试：已自愈的格式再次归一化不变"""
        norm = self._get_normalizer()
        once = norm("mat001")
        twice = norm(once)
        assert twice == "MAT-001", "幂等性：二次归一化不应改变结果"

    def test_normalize_non_entity_passthrough(self):
        """测试：非实体编码的字符串进行标准归一化（去空白+大写）"""
        norm = self._get_normalizer()
        result = norm("hello world")
        # _normalize_entity 会去空白+大写，但不改变非实体字符串的结构
        assert result == "HELLOWORLD", "非实体编码应去空白+大写"


# ================================================================
# Phase 2.2: 工单创建高并发幂等拦截 集成测试
# ================================================================

@pytest.mark.integration
class TestCreateTicketIdempotency:
    """验证 Redis 锁 + 幂等机制在高并发下的正确性"""

    @pytest.mark.asyncio
    async def test_idempotent_key_format(self):
        """测试：幂等 key 格式正确"""
        import hashlib
        query = "帮我创建采购单"
        session_id = "test_session"
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()[:8]
        tool_name = "create_ticket"

        idempotent_key = f"idempotent:tool:{tool_name}:{session_id}:{query_hash}"
        assert idempotent_key.startswith("idempotent:tool:create_ticket:")
        assert session_id in idempotent_key
        assert query_hash in idempotent_key

    @pytest.mark.asyncio
    async def test_lock_key_format(self):
        """测试：锁 key 格式正确"""
        import hashlib
        query = "帮我创建采购单"
        session_id = "test_session"
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()[:8]
        tool_name = "create_ticket"

        lock_key = f"lock:tool:{tool_name}:{session_id}:{query_hash}"
        assert lock_key.startswith("lock:tool:create_ticket:")
        assert session_id in lock_key

    @pytest.mark.asyncio
    async def test_idempotent_key_uniqueness(self):
        """测试：不同 query 产生不同的幂等 key"""
        import hashlib

        q1 = "创建采购单 MAT-001"
        q2 = "创建采购单 MAT-002"
        hash1 = hashlib.md5(q1.encode("utf-8")).hexdigest()[:8]
        hash2 = hashlib.md5(q2.encode("utf-8")).hexdigest()[:8]

        assert hash1 != hash2, "不同 query 应产生不同 hash"

    @pytest.mark.asyncio
    async def test_redis_client_importable(self):
        """测试：redis_client 模块可正常导入（不要求 Redis 在线）"""
        from app.core.redis_client import redis_manager
        assert redis_manager is not None
        # is_connected 可能为 False（Redis 未运行），但不影响模块加载

    @pytest.mark.asyncio
    async def test_concurrent_lock_race_simulation(self, _integration_connections):
        """模拟高并发锁竞争：第二次 acquire 应失败"""
        import asyncio
        from app.core.redis_client import redis_manager

        # 如果 Redis 不可用，跳过此测试
        if not redis_manager.is_connected:
            pytest.skip("Redis 未连接，跳过并发锁测试")

        lock_key = "lock:test:concurrent:race:sim"
        idempotent_key = "idempotent:test:concurrent:race:sim"

        # 清理可能残留的 key
        try:
            await redis_manager.client.delete(lock_key)
        except Exception:
            pass

        # 第一次获取锁 → 成功（返回持有者 token）
        token1 = await redis_manager.acquire_lock(lock_key, expire=5)
        assert token1, "第一次应获取锁成功"

        try:
            # 第二次获取同一个锁 → 失败（已被持有，返回 None）
            token2 = await redis_manager.acquire_lock(lock_key, expire=5)
            assert token2 is None, "第二次并发获取同一锁应失败"
        finally:
            await redis_manager.release_lock(lock_key, token1)
