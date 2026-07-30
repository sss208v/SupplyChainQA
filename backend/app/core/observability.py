"""
SupplyChainRAG - 生产级可观测性模块 (Langfuse)

集成 Langfuse 全链路 Trace，在 SSE 流中透传 trace_id，
让面试官直观看到 Agent 每步推理的 Token 消耗、耗时和输入输出。

配置：在 .env 中设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST
未配置时优雅降级为本地 JSON 日志追踪，trace_id 始终生成。
"""
import enum
import json
import logging
import time
import uuid
from typing import Optional, Any

logger = logging.getLogger(__name__)


class TraceProviderState(enum.Enum):
    """Langfuse Trace Provider 的三态（v2.1 改进）

    旧实现用 None/False/instance 三个不同值表示状态，调用方难以区分。
    新实现用 enum 明确语义：
    - UNINITIALIZED: 首次调用前
    - DISABLED: 无 key 或初始化失败（已确定不可用，避免重复探测）
    - ENABLED: Langfuse 客户端实例
    """
    UNINITIALIZED = "uninitialized"
    DISABLED = "disabled"
    ENABLED = "enabled"


# v2.1：TraceProviderState 三态 + 客户端实例（ENABLED 时存 Langfuse client）
_trace_provider: TraceProviderState | Any = TraceProviderState.UNINITIALIZED
_local_traces: dict = {}  # 本地追踪缓存 {trace_id: [spans]}


def _get_langfuse():
    """懒加载 Langfuse 客户端，使用 TraceProviderState 三态

    旧实现用 None/False/instance 三个值表示状态，调用方语义混乱：
    - _trace_provider is None（未初始化）→ 触发探测
    - _trace_provider is False（已禁用）→ 仍触发探测（bug）
    - _trace_provider is Langfuse（已连接）→ 直接返回

    v2.1 修复：只有 UNINITIALIZED 触发探测，DISABLED/ENABLED 直接返回。
    """
    global _trace_provider

    # 快速路径：已确定状态（非 UNINITIALIZED）→ 直接返回
    if _trace_provider is not TraceProviderState.UNINITIALIZED:
        if _trace_provider is TraceProviderState.DISABLED:
            return None
        # ENABLED：返回客户端实例
        return _trace_provider

    from app.config import get_settings
    settings = get_settings()
    pk = getattr(settings, "LANGFUSE_PUBLIC_KEY", "") or ""
    sk = getattr(settings, "LANGFUSE_SECRET_KEY", "") or ""
    host = getattr(settings, "LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not pk or not sk:
        logger.info("[Langfuse] 未配置 LANGFUSE key，禁用 Trace")
        _trace_provider = TraceProviderState.DISABLED
        return None

    try:
        import langfuse
        _trace_provider = langfuse.Langfuse(
            public_key=pk,
            secret_key=sk,
            host=host,
        )
        logger.info("[Langfuse] 客户端初始化成功: %s", host)
        return _trace_provider
    except ImportError:
        logger.warning("[Langfuse] langfuse 包未安装，pip install langfuse")
        _trace_provider = TraceProviderState.DISABLED
        return None
    except Exception as e:
        logger.warning("[Langfuse] 初始化失败: %s", e)
        _trace_provider = TraceProviderState.DISABLED
        return None


def get_trace_id() -> str:
    """生成全局唯一的 trace_id"""
    return str(uuid.uuid4())[:12]


def create_trace(name: str, trace_id: str, metadata: dict = None) -> Optional[dict]:
    """创建 Langfuse Trace

    Returns:
        trace 对象或 None（Langfuse 未配置时）
    """
    lf = _get_langfuse()
    if not lf:
        return None

    try:
        trace = lf.trace(
            name=name,
            id=trace_id,
            metadata=metadata or {},
        )
        return trace
    except Exception as e:
        logger.debug("[Langfuse] create_trace 失败: %s", e)
        return None


def get_langfuse_url(trace_id: str) -> str:
    """返回 Langfuse Trace 调试 URL"""
    from app.config import get_settings
    settings = get_settings()
    host = getattr(settings, "LANGFUSE_HOST", "https://cloud.langfuse.com")
    return f"{host.rstrip('/')}/trace/{trace_id}"


def is_enabled() -> bool:
    """追踪始终启用（Langfuse 或本地降级）"""
    return True


def _record_local(trace_id: str, span_name: str, input_data: dict = None, output_data: dict = None, metadata: dict = None):
    """本地 JSON 日志追踪（Langfuse 不可用时的降级）"""
    span = {
        "trace_id": trace_id,
        "span": span_name,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if input_data:
        span["input"] = {k: str(v)[:200] for k, v in input_data.items()}
    if output_data:
        span["output"] = {k: str(v)[:200] for k, v in output_data.items()}
    if metadata:
        span["metadata"] = metadata
    # 缓存到内存
    if trace_id not in _local_traces:
        _local_traces[trace_id] = []
    _local_traces[trace_id].append(span)
    # 同时输出到 logger
    logger.info(f"[Trace] {trace_id} | {span_name} | {json.dumps(metadata or {}, ensure_ascii=False)}")


def get_langfuse_callback(trace_id: str = None):
    """获取 LangChain 专用的 Langfuse CallbackHandler

    将 Langfuse Callback 注入 LangChain/LangGraph 的执行 config 中，
    实现全链路 Trace 采集：Token 消耗、每步耗时、输入输出。

    Args:
        trace_id: 关联的 Trace ID，用于将多个 span 归入同一 Trace

    Returns:
        CallbackHandler 或 None（Langfuse 未配置/未安装时）
    """
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
            trace_id=trace_id,
        )
    except ImportError:
        logger.warning("[Langfuse] langfuse.callback 不可用，需 pip install langfuse")
        return None
    except Exception as e:
        logger.warning("[Langfuse] 无法创建 CallbackHandler: %s", e)
        return None
# ---- Span 级追踪 ----

class TraceSpan:
    """Langfuse Span 上下文管理器，用于记录 Agent 每一步操作
    
    使用方式:
        async with TraceSpan("意图路由", trace_id, metadata={"method": "rule"}) as span:
            result = await router.route(query)
            span.update(output=result)
    """
    
    def __init__(self, name: str, trace_id: str, metadata: dict = None, input_data: dict = None):
        self.name = name
        self.trace_id = trace_id
        self.metadata = metadata or {}
        self.input_data = input_data
        self._span = None
        self._lf = None
    
    async def __aenter__(self):
        self._lf = _get_langfuse()
        if self._lf and self.trace_id:
            try:
                trace = self._lf.trace(id=self.trace_id, name="chat")
                self._span = trace.span(
                    name=self.name,
                    metadata=self.metadata,
                    input=self.input_data,
                )
            except Exception as e:
                logger.debug(f"[Langfuse] span create failed: {e}")
        return self
    
    async def __aexit__(self, *args):
        if self._span:
            try:
                self._span.end()
                self._lf.flush()
            except Exception as e:
                logger.debug(f"[Langfuse] span结束/刷新失败: {e}")
    
    def update(self, output: dict = None, metadata: dict = None, level: str = None):
        """更新 span 的输出和元数据"""
        if self._span:
            try:
                if output:
                    self._span.update(output=output)
                if metadata:
                    self._span.update(metadata=metadata)
                if level:
                    self._span.update(level=level)
            except Exception as e:
                logger.debug(f"[Langfuse] span更新失败: {e}")
    
    def set_error(self, error: str):
        """标记 span 为错误"""
        if self._span:
            try:
                self._span.update(level="ERROR", status_message=error)
            except Exception as e:
                logger.debug(f"[Langfuse] span错误标记失败: {e}")


def record_router_decision(trace_id: str, query: str, intent: str, method: str, confidence: float, duration_ms: int):
    """记录意图路由决策"""
    _record_local(trace_id, "意图路由", {"query": query}, {"intent": intent, "method": method, "confidence": confidence}, {"duration_ms": duration_ms})
    lf = _get_langfuse()
    if not lf or not trace_id:
        return
    try:
        trace = lf.trace(id=trace_id, name="chat")
        trace.span(
            name="意图路由",
            input={"query": query},
            output={"intent": intent, "method": method, "confidence": confidence},
            metadata={"duration_ms": duration_ms},
        ).end()
        lf.flush()
    except Exception as e:
        logger.debug(f"[Langfuse] record_router failed: {e}")


def record_tool_call(trace_id: str, tool_name: str, tool_input: str, tool_output: str, duration_ms: int, error: str = None):
    """记录工具调用"""
    _record_local(trace_id, f"工具调用: {tool_name}", {"tool": tool_name, "input": tool_input}, {"result": tool_output[:500]}, {"duration_ms": duration_ms, "error": error})
    lf = _get_langfuse()
    if not lf or not trace_id:
        return
    try:
        trace = lf.trace(id=trace_id, name="chat")
        span = trace.span(
            name=f"工具调用: {tool_name}",
            input={"tool": tool_name, "input": tool_input},
            output={"result": tool_output[:500]},
            metadata={"duration_ms": duration_ms},
        )
        if error:
            span.update(level="ERROR", status_message=error)
        span.end()
        lf.flush()
    except Exception as e:
        logger.debug(f"[Langfuse] record_tool failed: {e}")


def record_rag_retrieval(trace_id: str, query: str, num_chunks: int, sources: list, duration_ms: int):
    """记录 RAG 检索"""
    _record_local(trace_id, "RAG检索", {"query": query}, {"num_chunks": num_chunks}, {"duration_ms": duration_ms})
    lf = _get_langfuse()
    if not lf or not trace_id:
        return
    try:
        trace = lf.trace(id=trace_id, name="chat")
        trace.span(
            name="RAG检索",
            input={"query": query},
            output={"num_chunks": num_chunks, "sources": [s.get("source", "") for s in sources[:5]]},
            metadata={"duration_ms": duration_ms},
        ).end()
        lf.flush()
    except Exception as e:
        logger.debug(f"[Langfuse] record_rag failed: {e}")


def record_llm_generation(trace_id: str, model: str, prompt_tokens: int, completion_tokens: int, duration_ms: int, cost_yuan: float):
    """记录 LLM 生成统计

    数据完整性修复：之前无 Langfuse 时完全丢失 LLM 生成统计
    （router/tool/rag 都会落 _local_traces 缓存，LLM 不会）。
    现在补 _record_local 兜底，保证所有 record_* 调用都留痕。
    """
    # 1. 始终先落本地缓存（不依赖 Langfuse）
    _record_local(
        trace_id,
        f"LLM生成: {model}",
        {"model": model},
        {"tokens": prompt_tokens + completion_tokens, "cost_yuan": cost_yuan},
        {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "duration_ms": duration_ms,
            "cost_yuan": cost_yuan,
        },
    )

    # 2. 尝试同步到 Langfuse（如果有配置）
    lf = _get_langfuse()
    if not lf or not trace_id:
        return
    try:
        trace = lf.trace(id=trace_id, name="chat")
        trace.span(
            name=f"LLM生成: {model}",
            output={"tokens": prompt_tokens + completion_tokens, "cost_yuan": cost_yuan},
            metadata={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "duration_ms": duration_ms,
                "cost_yuan": cost_yuan,
            },
        ).end()
        lf.flush()
    except Exception as e:
        logger.debug(f"[Langfuse] record_llm failed: {e}")
