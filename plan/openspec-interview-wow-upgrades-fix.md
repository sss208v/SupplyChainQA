# SmartQA Pro — 面试专属“降维打击”高级功能闭环升级 OpenSpec (Bugfix & Reinforce)

> **版本**: 3.1 | **日期**: 2026-05-20 | **定位**: 针对 REQ-2 和 REQ-3 的业务未完全串联问题进行精准补强
> **升级执行人**: Hermes Agent
> **验证人**: Antigravity

本规格说明书针对当前 SmartQA Pro 中“四大面试硬核功能”的审计发现进行定向升级。通过完善后端业务流的闭环串联，确保 Langfuse 全链路 Trace 可以采集到大模型及 Agent 的思考细节，并彻底打通 Redis 分布式锁与幂等校验防脏数据的最后一公里。

---

## 1. 核心痛点与优化规格

### FIX-1：REQ-2 可观测性 (Langfuse) 全链路 Trace 采集闭环 [P0]
*   **当前问题**：虽然 SSE 接口已返回 `trace_id` 和 `langfuse_url` 并在前端进行了优雅呈现，但后端大模型调用及 Agent 推理引擎实际未注入 `CallbackHandler`，导致 Langfuse 控制台内只有空 Trace，无法监控到大模型实际的 Token 消耗、提示词和耗时。
*   **解决方案**：
    1.  **在 `observability.py` 中新增 Callback 构造器**：实现 `get_langfuse_callback(trace_id: str) -> Optional[CallbackHandler]`，用于获取特定 trace_id 的 LangChain 专用回调处理器。
    2.  **在 `LLMFactory` 中支持 Callback 注入**：为 `LLMFactory.astream`、`LLMFactory._raw_astream` 和 `LLMFactory.ainvoke` 引入可选的 `callbacks: Optional[list] = None` 参数，并传入底层大模型组件的 `config`。
    3.  **在 `LangChainAgent` 中支持 Callback 注入**：为 `LangChainAgent.run` 引入 `callbacks: Optional[list] = None`，在调用 `AgentExecutor.ainvoke` 时传入配置，从而捕获 Agent 复杂的 ReAct 思考过程、工具调用链与元数据。
    4.  **在 API 主流程中动态挂载**：在 `app/api/chat.py` 的 SSE 循环中，生成 `trace_id` 后动态构造 `langfuse_handler`，并将其自动透传给 LLM 或是 Agent 执行体。

*   **修改文件**：
    *   `backend/app/core/observability.py`
    *   `backend/app/core/llm_router.py`
    *   `backend/app/agents/langchain_agent.py`
    *   `backend/app/api/chat.py`

---

### FIX-2：REQ-3 Redis 分布式锁与敏感写操作幂等业务串联闭环 [P0]
*   **当前问题**：底层已封装了 Redis 分布式悲观锁与幂等校验，但业务流（如审批通过后执行的 `create_ticket` 工具）在 API 或引擎执行处处于“裸跑”状态，并发请求或重复点击将直接导致 SQLite 重复写入脏数据，面试中极易被当场问倒。
*   **解决方案**：
    1.  **在写操作审批执行处加锁与防重**：
        当用户完成审批确认重新发送请求（`body.approved` 为 `True` 且 `body.approved_tool == "create_ticket"`），在 `chat.py` 触发 `agent.run` 之前进行高并发加锁与幂等校验：
        *   **幂等键与锁键生成**：
            ```python
            import hashlib
            query_hash = hashlib.md5(safe_query.encode("utf-8")).hexdigest()[:8]
            lock_key = f"lock:tool:{tool_name}:{session_id}:{query_hash}"
            idempotent_key = f"idempotent:tool:{tool_name}:{session_id}:{query_hash}"
            ```
        *   **幂等状态拦截**：在执行前，先调用 `redis_manager.check_idempotent(idempotent_key)`，若已被标记 `completed`，直接拦截并向前端推送 SSE 友好提示（`type: "error"`, `content: "检测到该操作已成功执行，已自动幂等拦截，避免脏数据。"`），优雅降级。
        *   **悲观锁竞争**：尝试抢占分布式锁 `redis_manager.acquire_lock(lock_key, expire=15)`。若失败，直接拦截并推送正在处理中的 SSE 提示。
    2.  **状态流转与解锁闭环**：
        *   工具执行成功后，标记 `redis_manager.mark_idempotent(idempotent_key, ttl=300)` 状态（保留 5 分钟），同时释放分布式锁。
        *   使用 `try-finally` 结构，确保在遭遇系统崩溃或未预料异常时，分布式锁一定会在 `finally` 中被安全释放，防止死锁。

*   **修改文件**：
    *   `backend/app/api/chat.py`

---

## 2. 核心代码修改规范 (规格说明)

### 2.1 `backend/app/core/observability.py` 升级细节
新增 `get_langfuse_callback` 函数：
```python
def get_langfuse_callback(trace_id: str = None):
    """获取 LangChain 专用的 Langfuse CallbackHandler"""
    lf = _get_langfuse()
    if not lf:
        return None
    try:
        from langfuse.callback import CallbackHandler
        from app.config import get_settings
        settings = get_settings()
        pk = getattr(settings, "LANGFUSE_PUBLIC_KEY", "") or ""
        sk = getattr(settings, "LANGFUSE_SECRET_KEY", "") or ""
        host = getattr(settings, "LANGFUSE_HOST", "https://cloud.langfuse.com")
        return CallbackHandler(
            public_key=pk,
            secret_key=sk,
            host=host,
            trace_id=trace_id
        )
    except Exception as e:
        logger.warning("[Langfuse] 无法创建 CallbackHandler: %s", e)
        return None
```

### 2.2 `backend/app/core/llm_router.py` 升级细节
在 `_raw_astream` 和 `astream` 签名中支持 `callbacks` 参数传导：
```python
    @classmethod
    async def _raw_astream(
        cls,
        messages: list[BaseMessage],
        provider: Optional[str] = None,
        temperature: float = 0.7,
        callbacks: Optional[list] = None,
    ) -> AsyncIterator:
        llm = cls.get_llm(provider, temperature, streaming=True)
        provider_name = provider or settings.LLM_PROVIDER
        model_name = cls._get_model_name(provider_name)
        last_chunk = None
        
        # 传入 callbacks 参数
        config = {"callbacks": callbacks} if callbacks else None
        async for chunk in llm.astream(messages, config=config):
            last_chunk = chunk
            yield chunk
```
并在 `astream` 级透传。

### 2.3 `backend/app/agents/langchain_agent.py` 升级细节
在 `run` 签名中支持 `callbacks`：
```python
    async def run(
        self,
        query: str,
        tool_names: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        callbacks: Optional[list] = None,
    ) -> dict:
```
在执行 Agent 调用时：
```python
            # 执行 Agent 并附带 Trace Callback
            result = await agent_executor.ainvoke(
                {"input": enhanced_query},
                config={"callbacks": callbacks} if callbacks else None
            )
```

### 2.4 `backend/app/api/chat.py` 串联逻辑
#### (1) Langfuse 串联
在 `chat.py` 中捕获 `trace_id` 之后，生成 `langfuse_handler`：
```python
            from app.core.observability import get_trace_id, get_langfuse_url, is_enabled, get_langfuse_callback
            trace_id = get_trace_id()
            langfuse_handler = get_langfuse_callback(trace_id=trace_id)
            langfuse_callbacks = [langfuse_handler] if langfuse_handler else None
```
在流式大模型生成时：
```python
                    async for chunk in LLMFactory.astream(messages, callbacks=langfuse_callbacks):
```
在 Agent 运行时：
```python
                result = await agent.run(
                    query=safe_query,
                    tool_names=[tool_name] if tool_name else None,
                    session_id=session_id,
                    user_id=_user_id,
                    callbacks=langfuse_callbacks, # 注入！
                )
```

#### (2) Redis 分布式锁与幂等校验串联
在 `chat.py` 审批拦截流程下方（973 行左右），插入锁控制块：
```python
                # ---- Redis 并发锁与幂等校验 (FIX-2) ----
                from app.core.redis_client import redis_manager
                lock_key = None
                idempotent_key = None
                
                if redis_manager.is_connected:
                    import hashlib
                    query_hash = hashlib.md5(safe_query.encode("utf-8")).hexdigest()[:8]
                    lock_key = f"lock:tool:{tool_name}:{session_id}:{query_hash}"
                    idempotent_key = f"idempotent:tool:{tool_name}:{session_id}:{query_hash}"
                    
                    # 1. 幂等校验
                    if await redis_manager.check_idempotent(idempotent_key):
                        logger.warning(f"[Idempotency] 检测到重复请求并成功拦截: {idempotent_key}")
                        yield _sse_format({
                            "type": "error",
                            "message": "该操作已成功执行，请勿重复提交！",
                        })
                        yield _sse_format({
                            "type": "content",
                            "content": f"⚠️ **高并发幂等拦截**：检测到您刚刚重复发起了 **{tool_name}** 审批申请（该工单已创建成功），系统已自动拦截，防止脏数据写入数据库。",
                        })
                        yield "data: [DONE]\n\n"
                        return
                    
                    # 2. 分布式锁竞争
                    lock_acquired = await redis_manager.acquire_lock(lock_key, expire=15)
                    if not lock_acquired:
                        logger.warning(f"[RedisLock] 抢占分布式锁失败: {lock_key}")
                        yield _sse_format({
                            "type": "error",
                            "message": "系统正在处理中，请稍候...",
                        })
                        yield _sse_format({
                            "type": "content",
                            "content": f"⚠️ **并发安全拦截**：系统检测到该敏感写操作正在后台加速处理中，请勿频繁双击或并发重试。",
                        })
                        yield "data: [DONE]\n\n"
                        return
                
                try:
                    agent = _get_tool_agent(body.agent_type, tool_name)
                    # 支持接收 callbacks 参数（若存在）
                    run_kwargs = {
                        "query": safe_query,
                        "tool_names": [tool_name] if tool_name else None,
                        "session_id": session_id,
                        "user_id": _user_id,
                    }
                    if hasattr(agent, "run") and "callbacks" in agent.run.__code__.co_varnames:
                        run_kwargs["callbacks"] = langfuse_callbacks
                        
                    result = await agent.run(**run_kwargs)
                    _t_gen = time.perf_counter() - _t2
                    
                    # 3. 标记幂等成功
                    if idempotent_key and redis_manager.is_connected:
                        await redis_manager.mark_idempotent(idempotent_key, ttl=300)
                finally:
                    # 4. 确保释放锁
                    if lock_key and redis_manager.is_connected:
                        await redis_manager.release_lock(lock_key)
```

---

## 3. 验证与演示说明

1.  **Langfuse 验证**：
    *   在后台 `.env` 配置好 Langfuse 的秘钥后，发起对话，点击前端输出的调试 Trace 链接，能够完整看到 `ChatOpenAI` 节点及其流式 token 产出、耗时、输入输出的详细链路图。
2.  **高并发幂等锁验证**：
    *   在前端点击审批确认时，如果使用网络并发工具（如 `ab`）或者快速双击模拟重放，系统应该只执行一次工单创建，第二次及以后的请求应直接返回“⚠️ **高并发幂等拦截**...” 的系统拦截面板，控制台能清晰看到 `[Idempotency]` 的拦截日志。
