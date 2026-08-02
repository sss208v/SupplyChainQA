"""Orchestrator plan() 异常处理测试 — 验证 NameError 隐患（审查 Issue #5）

背景：orchestrator.py 的 plan() 中，若 llm.ainvoke() 抛出 json.JSONDecodeError，
外层 `except json.JSONDecodeError` 分支会引用未定义的局部变量 content，
导致 UnboundLocalError/NameError 掩盖原始错误。
"""
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestOrchestratorPlanErrorHandling:
    """plan() 异常路径测试（mock LLM，不依赖真实服务）"""

    def _fake_llm(self, side_effect):
        """构造 mock LLM：ainvoke 按 side_effect 抛出/返回"""
        fake = MagicMock()
        fake.ainvoke = AsyncMock(side_effect=side_effect)
        return fake

    async def _plan_with(self, fake_llm):
        """在 mock LLM 下调用 orchestrator.plan()"""
        from app.agents.orchestrator import Orchestrator
        from app.core.llm_router import LLMFactory

        o = Orchestrator()
        with patch.object(LLMFactory, "get_llm", return_value=fake_llm):
            return await o.plan("评估库存短缺风险")

    def test_plan_ainvoke_json_decode_error_no_unboundlocal(self):
        """ainvoke 抛 json.JSONDecodeError 时不应抛 UnboundLocalError/NameError（回归 #5）"""
        import asyncio

        async def _run():
            fake = self._fake_llm(json.JSONDecodeError("LLM 响应解析失败", "resp", 0))
            try:
                result = await self._plan_with(fake)
            except UnboundLocalError as e:
                pytest.fail(f"plan() 抛 UnboundLocalError（content 未定义被引用）: {e}")
            except NameError as e:
                pytest.fail(f"plan() 抛 NameError（content 未定义被引用）: {e}")
            # 修复后应优雅返回 error dict
            assert result["steps"] == []
            assert "error" in result
            return result

        asyncio.run(_run())

    def test_plan_ainvoke_generic_error_graceful(self):
        """ainvoke 抛一般异常（RuntimeError）应返回 error dict，不向上抛"""
        import asyncio

        async def _run():
            fake = self._fake_llm(RuntimeError("LLM 服务不可用"))
            result = await self._plan_with(fake)
            assert result["steps"] == []
            assert "LLM 服务不可用" in result["error"]
            return result

        asyncio.run(_run())

    def test_plan_success_returns_plan(self):
        """正常路径：ainvoke 返回合法 JSON 时应返回 plan（含 steps）"""
        import asyncio

        class _Resp:
            content = '{"goal": "查库存", "steps": [{"agent": "InventoryAgent", "task": "查 MAT-001 库存"}]}'

        async def _run():
            fake = MagicMock()
            fake.ainvoke = AsyncMock(return_value=_Resp())
            result = await self._plan_with(fake)
            assert result["goal"] == "查库存"
            assert len(result["steps"]) == 1
            assert result["steps"][0]["agent"] == "InventoryAgent"
            return result

        asyncio.run(_run())

    def test_plan_parse_error_graceful(self):
        """ainvoke 成功但返回非 JSON 内容 → 内层 except 返回 error dict"""
        import asyncio

        class _Resp:
            content = "抱歉，我无法生成计划"  # 非 JSON

        async def _run():
            fake = MagicMock()
            fake.ainvoke = AsyncMock(return_value=_Resp())
            result = await self._plan_with(fake)
            assert result["steps"] == []
            assert "error" in result
            return result

        asyncio.run(_run())

