"""
TOOL_CALL intent handler — 工具调用

包含：澄清检查、权限检查、审批检查、Redis并发锁+幂等校验、
Agent执行、结果格式化等完整流程。
"""
import hashlib
import logging
import time
from collections.abc import AsyncGenerator

from app.agents.agent_router import get_agent_for_tool
from app.api.chat_helpers import ChatRequest, _role_label, sse_event
from app.api.tool import WRITE_TOOLS, _is_tool_allowed
from app.config import get_settings
from app.core.clarify import check_needs_clarification
from app.core.redis_client import RedisManager

logger = logging.getLogger(__name__)
settings = get_settings()

__all__ = ["handle_tool_call"]


async def handle_tool_call(
    safe_query: str,
    tool_name: str,
    session_id: str,
    user_id: str,
    agent_type: str | None,
    body: ChatRequest,
    langfuse_callbacks: list | None,
    redis: RedisManager,
    user_role: str = "finance",
    user_level: str = "manager",
    needs_clarify: bool = False,
) -> AsyncGenerator[str, None]:
    """处理工具调用意图

    包含：澄清检查 → 权限检查 → 审批检查 → Redis锁+幂等 → agent.run → 释放锁
    """

    # ---- 澄清检查：参数不足时主动问用户 ----
    clarify = check_needs_clarification(safe_query, tool_name)
    needs_clarify_by_llm = needs_clarify
    if (clarify and clarify.needs_clarification) or needs_clarify_by_llm:
        logger.info(f"[Clarify] 需要澄清: tool={tool_name} missing={clarify.missing_params if clarify else None}")
        question = clarify.question if clarify else "请问您想查询哪个物料的库存？可以提供物料编码（如 MAT-001）或物料名称。"
        missing = clarify.missing_params if clarify else ["material_code"]
        yield sse_event("clarify", question=question, tool=tool_name, missing_params=missing)
        yield sse_event("content", content=question)
        logger.info("[Clarify] 已发送澄清提问")
        return

    # ---- 工具权限检查（部门 × 级别 二维）----
    if not _is_tool_allowed(tool_name, user_role, user_level):
        logger.warning(f"[Permission] 用户角色 {user_role} 级别 {user_level} 无权调用工具 {tool_name}")
        yield sse_event("tool_blocked", tool=tool_name, reason=f"您的角色「{_role_label(user_role)}」无权执行此操作")
        yield sse_event("content", content=f"[警告] 无权执行 **{tool_name}** 操作。如需权限，请联系管理员。")
        return

    # 发送工具调用状态
    yield sse_event("tool_status", status="calling", tool=tool_name)

    # ---- 写操作审批检查 ----
    if tool_name in WRITE_TOOLS and (not body.approved or body.approved_tool != tool_name):
        logger.info(f"[Approval] 写操作需要审批: tool={tool_name}")
        yield sse_event("approval_request", tool=tool_name, query=safe_query, message=f"即将执行写操作：{tool_name}，请确认是否继续。")
        yield sse_event("content", content=f"[警告] 即将执行 **{tool_name}** 操作，请点击「确认执行」继续。")
        return

    # ---- Redis 并发锁与幂等校验 ----
    lock_key = None
    lock_token = None
    idempotent_key = None
    idempotent_acquired = False
    if redis.is_connected:
        query_hash = hashlib.md5(safe_query.encode("utf-8")).hexdigest()[:8]
        lock_key = f"lock:tool:{tool_name}:{session_id}:{query_hash}"
        idempotent_key = f"idempotent:tool:{tool_name}:{session_id}:{query_hash}"

        # 1. 幂等抢占（SET NX 原子三态，消除检查-执行竞态）
        idem_state = await redis.try_begin_idempotent(idempotent_key)
        if idem_state == "completed":
            logger.warning(f"[Idempotency] 检测到重复请求并成功拦截: {idempotent_key}")
            yield sse_event("error", message="该操作已成功执行，请勿重复提交！")
            yield sse_event("content", content=f"[警告] **高并发幂等拦截**：系统检测到您已成功执行 **{tool_name}** 操作（该工单已创建），已自动拦截重复请求，防止脏数据写入数据库。")
            return
        if idem_state == "pending":
            logger.warning(f"[Idempotency] 相同请求正在处理中: {idempotent_key}")
            yield sse_event("error", message="系统正在处理中，请稍候...")
            yield sse_event("content", content=f"[警告] **并发安全拦截**：检测到 **{tool_name}** 操作正在后台处理中，请勿频繁双击或并发重试。")
            return
        idempotent_acquired = True

        # 2. 分布式锁竞争（与幂等双保险）
        lock_token = await redis.acquire_lock(lock_key, expire=settings.TOOL_LOCK_EXPIRE)
        if not lock_token:
            logger.warning(f"[RedisLock] 抢占分布式锁失败: {lock_key}")
            # 未执行，撤销 pending 标记允许重试
            await redis.cancel_idempotent(idempotent_key)
            yield sse_event("error", message="系统正在处理中，请稍候...")
            yield sse_event("content", content=f"[警告] **并发安全拦截**：检测到 **{tool_name}** 操作正在后台处理中，请勿频繁双击或并发重试。")
            return

    succeeded = False
    try:
        agent = _get_tool_agent(agent_type, tool_name)
        run_kwargs = {
            "query": safe_query,
            "tool_names": [tool_name] if tool_name else None,
            "session_id": session_id,
            "user_id": user_id,
        }
        if langfuse_callbacks and agent_type == "langchain":
            run_kwargs["callbacks"] = langfuse_callbacks
        result = await agent.run(**run_kwargs)

        # 3. 标记幂等成功
        if idempotent_key and redis.is_connected:
            await redis.mark_idempotent(idempotent_key)
        succeeded = True
    finally:
        # 执行失败时撤销 pending 标记，否则用户会被错误拦截至 TTL 过期
        if not succeeded and idempotent_acquired and redis.is_connected:
            await redis.cancel_idempotent(idempotent_key)
        # 4. 确保释放锁（仅释放自己持有的 token）
        if lock_token and redis.is_connected:
            await redis.release_lock(lock_key, lock_token)

    _t_gen = time.perf_counter()  # timing placeholder

    # 发送工具调用结果
    for tc in result["tool_calls"]:
        yield sse_event("tool_call", tool=tc["tool"], input=tc["input"], observation=tc.get("observation", tc.get("result", "")))

    # 检查 DEMO_MODE 降级信息
    demo_info = result.get("demo_info")
    if demo_info:
        yield sse_event("demo_mode", mode=demo_info.get("mode", "demo"), reason=demo_info.get("reason", ""), summary=demo_info.get("summary", ""))

    # 发送最终回答
    yield sse_event("content", content=result["answer"])
    logger.info(f"[TOOL_CALL处理] tool={tool_name}")


def _get_tool_agent(agent_type: str | None = None, tool_name: str | None = None):
    """返回 Tool Agent — 按工具名路由到专域 Agent"""
    if agent_type in ("langgraph", "langchain"):
        logger.warning(f"[Chat] agent_type='{agent_type}' 已归档，回退到默认 Agent 路由")
    return get_agent_for_tool(tool_name)
