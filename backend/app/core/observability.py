"""
SmartQA Pro — 生产级可观测性模块 (Langfuse)

集成 Langfuse 全链路 Trace，在 SSE 流中透传 trace_id，
让面试官直观看到 Agent 每步推理的 Token 消耗、耗时和输入输出。

配置：在 .env 中设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST
未配置时优雅降级，不影响正常功能。
"""
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_trace_provider = None  # lazy init


def _get_langfuse():
    global _trace_provider
    if _trace_provider is not None:
        return _trace_provider

    from app.config import get_settings
    settings = get_settings()
    pk = getattr(settings, "LANGFUSE_PUBLIC_KEY", "") or ""
    sk = getattr(settings, "LANGFUSE_SECRET_KEY", "") or ""
    host = getattr(settings, "LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not pk or not sk:
        _trace_provider = False  # mark as disabled
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
        _trace_provider = False
        return None
    except Exception as e:
        logger.warning("[Langfuse] 初始化失败: %s", e)
        _trace_provider = False
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
    """检查 Langfuse 是否已配置并可用"""
    return _get_langfuse() is not None and _get_langfuse() is not False
