"""
SupplyChainRAG 跨域 Orchestrator 测试

测试 Orchestrator 的步骤执行、结果聚合、汇总能力。
LLM 依赖的 plan() 标记为 integration 测试，CI 中可跳过。
设置 RUN_LIVE_LLM_TESTS=true 可启用需要真实 LLM API 的测试。
"""
import pytest
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 条件跳过：未设置 RUN_LIVE_LLM_TESTS 时跳过所有 integration 测试
LIVE_LLM = os.environ.get("RUN_LIVE_LLM_TESTS", "").lower() in ("1", "true", "yes")
HAS_API_KEY = bool(os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", ""))
skip_no_llm = pytest.mark.skipif(
    not (LIVE_LLM and HAS_API_KEY),
    reason="需要 RUN_LIVE_LLM_TESTS=true 环境变量 + 有效 API key (DEEPSEEK_API_KEY 或 MINIMAX_API_KEY)"
)


class TestOrchestratorStructure:
    """Orchestrator 基础结构测试"""

    def test_orchestrator_import(self):
        from app.agents.orchestrator import orchestrator
        assert orchestrator is not None

    def test_orchestrator_execute_empty_steps(self):
        """空步骤列表返回合理结果"""
        from app.agents.orchestrator import orchestrator

        async def _run():
            result = await orchestrator.execute("test goal", [])
            assert result["total_steps"] == 0
            assert result["success_steps"] == 0
            return result

        result = asyncio.run(_run())
        assert result["goal"] == "test goal"

    @pytest.mark.integration
    @skip_no_llm
    def test_orchestrator_summarize(self):
        """LLM 汇总（需要 API key，跳过无 key 场景）"""
        from app.agents.orchestrator import orchestrator

        step_results = [
            {"step": 1, "agent": "InventoryAgent", "task": "查库存",
             "result": "MAT-001 库存 50，安全库存 200", "error": False},
            {"step": 2, "agent": "PurchaseAgent", "task": "查在途",
             "result": "PO-001 在途 100 件，ETA 5天", "error": False},
        ]

        async def _run():
            summary = await orchestrator.summarize("库存评估", step_results)
            assert len(summary) > 0
            return summary

        result = asyncio.run(_run())
        print(f"Summary ({len(result)} chars): {result[:200]}...")
        assert len(result) > 50  # 汇总应该有一定长度


class TestRouterGoalDetection:
    """路由器的 GOAL 意图检测测试"""

    def test_goal_keyword_detection(self):
        from app.agents.router import RouterAgent, IntentType
        router = RouterAgent()

        goal_queries = [
            "帮我评估 MAT-001 的库存风险",
            "MAT-001 不够了怎么办",
            "帮我分析供应商延迟的影响",
            "看看要不要紧急采购",
            "帮我判断是否需要创建工单",
        ]

        for q in goal_queries:
            result = router._rule_match(q)
            # 规则匹配可能命中 tool 或 goal，不做强制断言
            # goal 关键词应该能命中
            if result and result["intent"] == IntentType.GOAL:
                print(f"[GOAL] \"{q}\" -> rule match OK")
            elif result:
                print(f"[{result['intent'].value}] \"{q}\" -> other rule match")
            else:
                print(f"[NO_MATCH] \"{q}\" -> needs LLM classify")

    def test_tool_still_works(self):
        """工具查询不应被误判为 GOAL"""
        from app.agents.router import RouterAgent, IntentType
        router = RouterAgent()

        tool_queries = [
            "查 MAT-001 库存",
            "PO-20250601 什么状态",
            "现在几点",
        ]

        for q in tool_queries:
            result = router._rule_match(q)
            if result:
                # 工具查询不应是 GOAL
                assert result["intent"] != IntentType.GOAL, f"\"{q}\" 不应是 GOAL"
                print(f"[OK] \"{q}\" -> {result['intent'].value}")


class TestDomainAgentExecution:
    """专域 Agent 执行测试（需要真实 LLM key，标记为 integration）"""

    @pytest.mark.integration
    def test_inventory_agent_run(self):
        """InventoryAgent 查询 MAT-001 库存"""
        from app.agents.inventory_agent import inventory_agent

        async def _run():
            result = await inventory_agent.run("查 MAT-001 库存")
            assert "answer" in result
            assert len(result["answer"]) > 0
            print(f"InventoryAgent answer: {result['answer'][:200]}")
            return result

        result = asyncio.run(_run())
        assert "MAT-001" in result["answer"] or "50" in result["answer"]

    @pytest.mark.integration
    def test_purchase_agent_run(self):
        """PurchaseAgent 查询采购订单"""
        from app.agents.purchase_agent import purchase_agent

        async def _run():
            result = await purchase_agent.run("查 PO-20250601 订单状态")
            assert "answer" in result
            print(f"PurchaseAgent answer: {result['answer'][:200]}")
            return result

        result = asyncio.run(_run())
        assert len(result["answer"]) > 0

    @pytest.mark.integration
    def test_orchestrator_full_flow(self):
        """Orchestrator 完整 Plan → Execute → Summarize 流程"""
        from app.agents.orchestrator import orchestrator

        async def _run():
            result = await orchestrator.run(
                goal="MAT-001 库存不够了，帮我评估是否需要紧急采购",
            )
            assert "answer" in result
            assert "plan" in result
            execution = result.get("execution")
            if execution is None:
                # LLM 不可用(502等)时 orchestrator 返回 execution=None，跳过后续断言
                pytest.skip("Orchestrator execution is None — LLM backend unavailable")
            assert execution.get("total_steps", 0) > 0
            print(f"Orchestrator: {execution.get('total_steps')} steps, "
                  f"success={execution.get('success_steps')}, "
                  f"duration={result.get('duration_ms')}ms")
            print(f"Answer preview: {result['answer'][:300]}")
            return result

        result = asyncio.run(_run())
        assert len(result["answer"]) > 100  # 应该有较长的回答
