"""
SupplyChainRAG - 结构化日志模块
生产环境输出 JSON 格式（可接入 ELK/CloudWatch），开发环境保持人类可读格式。
"""
import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """将日志记录序列化为 JSON，支持结构化上下文字段。"""

    _RESERVED = frozenset({
        "name", "msg", "args", "created", "relativeCreated",
        "thread", "threadName", "process", "processName",
        "pathname", "filename", "module", "funcName", "lineno",
        "levelname", "levelno", "exc_info", "exc_text", "stack_info",
        "message", "taskName",
    })

    _CONTEXT_KEYS = (
        "session_id", "trace_id", "intent", "duration_ms",
        "user_id", "token_count", "tool_name",
    )

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        for key in self._CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    """开发环境的彩色人类可读格式。"""

    _COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:<8}{self._RESET}"
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        msg = record.getMessage()

        parts = [f"{ts} {level} {record.name}: {msg}"]

        if record.exc_info and record.exc_info[1] is not None:
            parts.append(self.formatException(record.exc_info))

        return "\n".join(parts)


def setup_logging(debug: bool = False) -> None:
    """配置全局日志。

    Args:
        debug: True 用人类可读格式，False 用 JSON 格式。
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(HumanFormatter() if debug else JSONFormatter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
