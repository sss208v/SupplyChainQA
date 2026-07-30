"""
GOAL intent handler — 多步编排

接收用户目标，通过 Orchestrator 拆解为多步执行计划并逐步执行。
"""
import asyncio
import logging
from typing import AsyncGenerator
from app.api.chat_helpers import sse_event
from app.agents.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

__all__ = ["handle_goal"]


async def handle_goal(
    safe_query: str,
    session_id: str,
    user_id: str,
    orchestrator: Orchestrator,
) -> AsyncGenerator[str, None]:
    """处理多步编排意图

    调用 Orchestrator 拆解目标为多步计划并逐步执行。
    带 120 秒超时保护。
    """
    # 发送编排开始事件
    yield sse_event("orchestrator_start", message="正在分析目标并拆解任务...")

    # 调用 Orchestrator（带超时保护）
    result = await asyncio.wait_for(
        orchestrator.run(
            goal=safe_query,
            session_id=session_id,
            user_id=user_id,
        ),
        timeout=120,
    )

    # 发送执行计划
    plan = result.get("plan", {})
    if plan.get("steps"):
        # 注意：sse_event 的首位参数是 event_type，kwargs 中不能含 "type" 键
        # （历史 bug：**plan_event 展开含 type 键导致 TypeError，plan 非空即崩）
        plan_kwargs = {
            "goal": plan.get("goal", ""),
            "steps": [
                {"step": i + 1, "agent": s.get("agent", ""), "task": s.get("task", "")}
                for i, s in enumerate(plan["steps"])
            ],
        }
        demo_info = plan.get("demo_info")
        if demo_info:
            plan_kwargs["demo_info"] = demo_info
        yield sse_event("orchestrator_plan", **plan_kwargs)

    # 发送每步执行结果
    execution = result.get("execution", {})
    for sr in execution.get("step_results", []):
        yield sse_event(
            "agent_step",
            step=sr["step"],
            agent=sr["agent"],
            task=sr["task"],
            status="error" if sr.get("error") else "done",
            duration_ms=sr.get("duration_ms", 0),
        )

    # 发送最终回答
    yield sse_event("content", content=result["answer"])
    logger.info(
        f"[GOAL编排处理] steps={execution.get('total_steps',0)} success={execution.get('success_steps',0)}"
    )
