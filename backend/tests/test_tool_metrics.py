"""tool_metrics 模块单元测试

覆盖 ToolMetricsCollector 的 record / stats / ring-buffer 溢出 / 退化场景。
纯内存操作，无外部服务依赖。
"""
import pytest

from app.core.tool_metrics import ToolMetricsCollector, tool_metrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def collector():
    """每个测试用例使用全新的 collector 实例，避免状态泄漏。"""
    return ToolMetricsCollector()


# ---------------------------------------------------------------------------
# test_record_tool_call
# ---------------------------------------------------------------------------

class TestRecordToolCall:
    def test_record_single_call(self, collector):
        """记录一次调用后，recent 中能查到。"""
        collector.record(
            tool_name="web_search",
            tool_input={"q": "hello"},
            tool_output="result text",
            duration_ms=123.4,
            success=True,
            session_id="sess-001",
        )
        recent = collector.recent(limit=10)
        assert len(recent) == 1

        r = recent[0]
        assert r["tool_name"] == "web_search"
        assert r["duration_ms"] == 123.4
        assert r["success"] is True
        assert r["session_id"] == "sess-001"
        assert "hello" in r["input_preview"]
        assert "result text" in r["output_preview"]

    def test_record_default_values(self, collector):
        """未指定可选参数时使用默认值。"""
        collector.record(tool_name="calc")
        r = collector.recent(limit=1)[0]
        assert r["tool_name"] == "calc"
        assert r["duration_ms"] == 0
        assert r["success"] is True
        assert r["session_id"] == ""


# ---------------------------------------------------------------------------
# test_ring_buffer_overflow
# ---------------------------------------------------------------------------

class TestRingBufferOverflow:
    def test_overflow_keeps_latest_entries(self, collector):
        """超过 max_records (1000) 后，旧记录被丢弃，保留最新 1000 条。"""
        # 写入 1010 条
        for i in range(1010):
            collector.record(tool_name=f"tool_{i}", duration_ms=i)

        recent = collector.recent(limit=2000)
        assert len(recent) == 1000

        # 最早保留的应该是 tool_10（index 10），最新的是 tool_1009
        assert recent[0]["tool_name"] == "tool_10"
        assert recent[-1]["tool_name"] == "tool_1009"

    def test_overflow_exactly_at_limit(self, collector):
        """恰好 1000 条不触发裁剪。"""
        for i in range(1000):
            collector.record(tool_name="t", duration_ms=1)
        assert len(collector.recent(limit=2000)) == 1000


# ---------------------------------------------------------------------------
# test_get_stats_per_tool
# ---------------------------------------------------------------------------

class TestGetStatsPerTool:
    def test_stats_groups_by_tool(self, collector):
        """stats 按 tool_name 分组统计。"""
        collector.record(tool_name="search", duration_ms=100, success=True)
        collector.record(tool_name="search", duration_ms=200, success=True)
        collector.record(tool_name="calc", duration_ms=50, success=False)

        s = collector.stats()

        assert "search" in s
        assert "calc" in s

        assert s["search"]["count"] == 2
        assert s["search"]["avg_ms"] == 150.0
        assert s["search"]["success_rate"] == 1.0

        assert s["calc"]["count"] == 1
        assert s["calc"]["avg_ms"] == 50.0
        assert s["calc"]["success_rate"] == 0.0

    def test_stats_summary(self, collector):
        """_summary 字段包含全局统计。"""
        collector.record(tool_name="a", success=True)
        collector.record(tool_name="a", success=True)
        collector.record(tool_name="b", success=False)

        s = collector.stats()
        summary = s["_summary"]
        assert summary["total_calls"] == 3
        assert summary["unique_tools"] == 2
        assert summary["total_success_rate"] == pytest.approx(2 / 3, abs=0.01)


# ---------------------------------------------------------------------------
# test_latency_percentiles
# ---------------------------------------------------------------------------

class TestLatencyPercentiles:
    def test_avg_ms_calculation(self, collector):
        """验证 avg_ms = total_ms / count。"""
        for i in range(1, 101):
            collector.record(tool_name="api", duration_ms=float(i))

        s = collector.stats()
        # 平均 = (1+2+...+100) / 100 = 5050 / 100 = 50.5
        assert s["api"]["avg_ms"] == pytest.approx(50.5, abs=0.1)

    def test_recent_sorting_preserves_order(self, collector):
        """recent 返回最后 N 条，保持插入顺序。"""
        for i in range(20):
            collector.record(tool_name=f"t{i}", duration_ms=float(i))

        recent = collector.recent(limit=5)
        names = [r["tool_name"] for r in recent]
        assert names == ["t15", "t16", "t17", "t18", "t19"]


# ---------------------------------------------------------------------------
# test_success_rate
# ---------------------------------------------------------------------------

class TestSuccessRate:
    def test_all_success(self, collector):
        for _ in range(10):
            collector.record(tool_name="x", success=True)
        assert collector.stats()["x"]["success_rate"] == 1.0

    def test_all_failure(self, collector):
        for _ in range(10):
            collector.record(tool_name="x", success=False)
        assert collector.stats()["x"]["success_rate"] == 0.0

    def test_mixed(self, collector):
        for _ in range(3):
            collector.record(tool_name="x", success=True)
        for _ in range(7):
            collector.record(tool_name="x", success=False)
        assert collector.stats()["x"]["success_rate"] == pytest.approx(0.3, abs=0.01)

    def test_summary_success_rate(self, collector):
        """_summary.total_success_rate 跨所有工具汇总。"""
        collector.record(tool_name="a", success=True)
        collector.record(tool_name="b", success=False)
        s = collector.stats()
        assert s["_summary"]["total_success_rate"] == 0.5


# ---------------------------------------------------------------------------
# test_empty_stats
# ---------------------------------------------------------------------------

class TestEmptyStats:
    def test_empty_collector_stats(self, collector):
        """空 collector 的 stats 返回仅含 _summary 的字典。"""
        s = collector.stats()
        assert "_summary" in s
        assert s["_summary"]["total_calls"] == 0
        assert s["_summary"]["total_success_rate"] == 0
        assert s["_summary"]["unique_tools"] == 0

    def test_empty_recent(self, collector):
        """空 collector 的 recent 返回空列表。"""
        assert collector.recent() == []

    def test_clear_resets_state(self, collector):
        """clear 后 stats 恢复空状态。"""
        collector.record(tool_name="x", duration_ms=99, success=True)
        collector.clear()
        s = collector.stats()
        assert s["_summary"]["total_calls"] == 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_singleton_exists(self):
        """模块级 tool_metrics 是 ToolMetricsCollector 实例。"""
        assert isinstance(tool_metrics, ToolMetricsCollector)
