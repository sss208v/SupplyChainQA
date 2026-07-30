"""
Agent 工具调用指标收集器

记录每次工具调用的 name / duration_ms / success，提供 per-tool 统计。
存储：内存 + 可选 SQLite 持久化。
"""
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class ToolMetricsCollector:
    """工具调用指标收集器（单例）"""

    def __init__(self):
        self._records: list[dict] = []
        self._max_records = 1000

    def record(
        self,
        tool_name: str,
        tool_input: dict = None,
        tool_output: str = "",
        duration_ms: float = 0,
        success: bool = True,
        session_id: str = "",
    ):
        """记录一次工具调用"""
        record = {
            "tool_name": tool_name,
            "input_preview": str(tool_input)[:100] if tool_input else "",
            "output_preview": str(tool_output)[:100],
            "duration_ms": round(duration_ms, 1),
            "success": success,
            "session_id": session_id[:20] if session_id else "",
            "timestamp": time.time(),
        }
        self._records.append(record)

        # 限制内存占用
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

        logger.debug(f"[Metrics] {tool_name}: {duration_ms:.0f}ms success={success}")

    def stats(self) -> dict:
        """返回 per-tool 统计"""
        tool_stats = defaultdict(lambda: {"count": 0, "total_ms": 0.0, "success": 0, "fail": 0})
        for r in self._records:
            s = tool_stats[r["tool_name"]]
            s["count"] += 1
            s["total_ms"] += r["duration_ms"]
            if r["success"]:
                s["success"] += 1
            else:
                s["fail"] += 1

        result = {}
        for name, s in sorted(tool_stats.items()):
            result[name] = {
                "count": s["count"],
                "avg_ms": round(s["total_ms"] / s["count"], 1) if s["count"] else 0,
                "success_rate": round(s["success"] / s["count"], 2) if s["count"] else 0,
            }

        total_count = sum(s["count"] for s in tool_stats.values())
        total_success = sum(s["success"] for s in tool_stats.values())
        result["_summary"] = {
            "total_calls": total_count,
            "total_success_rate": round(total_success / total_count, 2) if total_count else 0,
            "unique_tools": len(tool_stats),
        }

        return result

    def recent(self, limit: int = 50) -> list[dict]:
        """返回最近 N 条记录"""
        return self._records[-limit:]

    def clear(self):
        """清空记录"""
        self._records.clear()


# Module-level singleton
tool_metrics = ToolMetricsCollector()
