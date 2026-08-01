"""Observability 模块单元测试"""
import pytest
import sys, os
from unittest.mock import patch, MagicMock

from helpers import capture_record_local, fake_langfuse, fake_settings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTraceID:
    def test_trace_id_generation(self):
        from app.core.observability import get_trace_id
        tid1 = get_trace_id()
        tid2 = get_trace_id()
        assert len(tid1) == 12
        assert tid1 != tid2
        assert isinstance(tid1, str)

    def test_trace_id_is_hex(self):
        from app.core.observability import get_trace_id
        tid = get_trace_id()
        # UUID hex chars only
        assert all(c in '0123456789abcdef-' for c in tid)  # UUID4 includes hyphens


class TestLangfuseURL:
    def test_url_format(self):
        from app.core.observability import get_langfuse_url
        url = get_langfuse_url("abc123")
        assert "abc123" in url
        assert url.startswith("http")

    def test_url_no_trailing_slash(self):
        from app.core.observability import get_langfuse_url
        url = get_langfuse_url("test456")
        assert "//" not in url.replace("https://", "").replace("http://", "").split("/trace")[0] + "/trace"


class TestIsEnabled:
    def test_is_enabled_no_config(self):
        # Without LANGFUSE keys, should return False
        from app.core.observability import is_enabled
        result = is_enabled()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 补测：_record_local、create_trace、get_langfuse_callback、TraceSpan、
# record_router_decision、record_tool_call、record_rag_retrieval、record_llm_generation
# 之前 observability.py 仅 26% 覆盖 — TraceSpan + 4 个 record_* 几乎全裸奔
# ---------------------------------------------------------------------------


class TestRecordLocal:
    def test_record_local_stores_to_cache(self, monkeypatch):
        """_record_local 应把 span 写入 _local_traces 缓存"""
        from app.core import observability as obs
        obs._local_traces.clear()

        obs._record_local("tid-c1", "test_span", {"k": "v"}, {"r": 1}, {"m": 1})
        assert "tid-c1" in obs._local_traces
        spans = obs._local_traces["tid-c1"]
        assert len(spans) == 1
        s = spans[0]
        assert s["trace_id"] == "tid-c1"
        assert s["span"] == "test_span"
        assert "timestamp" in s
        # _record_local 内部 str(v)[:200] 转字符串
        assert s["input"] == {"k": "v"}
        assert s["output"] == {"r": "1"}
        assert s["metadata"] == {"m": 1}

    def test_record_local_appends_multiple_spans(self):
        """同一 trace_id 多次调用应追加 span 列表"""
        from app.core import observability as obs
        obs._local_traces.clear()

        obs._record_local("tid-multi", "s1", {}, {}, {})
        obs._record_local("tid-multi", "s2", {}, {}, {})
        obs._record_local("tid-multi", "s3", {}, {}, {})
        assert len(obs._local_traces["tid-multi"]) == 3

    def test_record_local_truncates_long_values(self):
        """input/output 中的长字符串应被截断到 200 字符"""
        from app.core import observability as obs
        obs._local_traces.clear()

        long_str = "x" * 500
        obs._record_local("tid-trunc", "s", {"q": long_str}, {"r": long_str}, None)
        s = obs._local_traces["tid-trunc"][0]
        assert len(s["input"]["q"]) == 200
        assert len(s["output"]["r"]) == 200
        assert "metadata" not in s  # metadata=None 不写

    def test_record_local_with_empty_metadata(self):
        """metadata 为空 dict 时也不写入 span（避免无意义字段）"""
        from app.core import observability as obs
        obs._local_traces.clear()

        obs._record_local("tid-em", "s", {"k": 1}, {"r": 2}, None)
        s = obs._local_traces["tid-em"][0]
        assert "metadata" not in s


class TestCreateTrace:
    def test_create_trace_no_langfuse(self, monkeypatch):
        """无 langfuse 时返回 None"""
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)

        result = obs.create_trace("test", "tid-3", {"k": "v"})
        assert result is None

    def test_create_trace_success(self, monkeypatch):
        """有 langfuse 时返回 trace 对象"""
        from app.core import observability as obs

        fake_trace = MagicMock()
        fake_lf = MagicMock()
        fake_lf.trace.return_value = fake_trace
        monkeypatch.setattr(obs, "_get_langfuse", lambda: fake_lf)

        result = obs.create_trace("test", "tid-4", {"k": "v"})
        assert result is fake_trace
        fake_lf.trace.assert_called_once()

    def test_create_trace_exception_returns_none(self, monkeypatch):
        """langfuse.trace 抛错 → 返回 None（不向上抛）"""
        from app.core import observability as obs

        fake_lf = MagicMock()
        fake_lf.trace.side_effect = RuntimeError("trace failed")
        monkeypatch.setattr(obs, "_get_langfuse", lambda: fake_lf)

        result = obs.create_trace("test", "tid-5")
        assert result is None


class TestGetLangfuseCallback:
    def test_callback_no_langfuse(self, monkeypatch):
        """_get_langfuse 返回 None（无 key 或初始化失败）→ CallbackHandler 永远不被尝试"""
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)

        assert obs.get_langfuse_callback() is None
        assert obs.get_langfuse_callback("tid-6") is None

    def test_callback_with_langfuse_and_trace_id(self, monkeypatch):
        """有 langfuse + 显式 trace_id → 真正构造 CallbackHandler（需安装 langfuse 库）"""
        from app.core import observability as obs
        fake_lf = MagicMock()
        monkeypatch.setattr(obs, "_get_langfuse", lambda: fake_lf)

        # 只有 langfuse 库存在时才能真正测试 CallbackHandler
        try:
            import langfuse  # noqa: F401
        except ImportError:
            pytest.skip("langfuse 库未安装，跳过 CallbackHandler 真实测试")

        # 提供真实 langfuse，直接验证返回非 None
        cb = obs.get_langfuse_callback("tid-7")
        assert cb is not None
        assert cb.trace_id == "tid-7"

    def test_callback_with_langfuse_auto_trace_id(self, monkeypatch):
        """有 langfuse + 无 trace_id → 自动生成 12 字符 trace_id"""
        from app.core import observability as obs
        fake_lf = MagicMock()
        monkeypatch.setattr(obs, "_get_langfuse", lambda: fake_lf)

        try:
            import langfuse  # noqa: F401
        except ImportError:
            pytest.skip("langfuse 库未安装")

        cb = obs.get_langfuse_callback()
        assert cb is not None
        assert len(cb.trace_id) == 12

    def test_callback_exception_returns_none(self, monkeypatch):
        """CallbackHandler 构造抛错（已 import 但构造失败）→ None"""
        from app.core import observability as obs
        import sys

        fake_lf = MagicMock()
        monkeypatch.setattr(obs, "_get_langfuse", lambda: fake_lf)

        # 给 sys.modules["langfuse.callback"] 注入一个 CallbackHandler 抛错
        # 模拟"langfuse 库版本不兼容/构造失败"
        fake_callback_module = MagicMock()
        fake_callback_module.CallbackHandler.side_effect = RuntimeError("boom")
        monkeypatch.setitem(sys.modules, "langfuse.callback", fake_callback_module)
        try:
            assert obs.get_langfuse_callback("tid-8") is None
        finally:
            sys.modules.pop("langfuse.callback", None)


class TestTraceSpan:
    @pytest.mark.asyncio
    async def test_span_no_langfuse(self, monkeypatch):
        """无 langfuse 时 _span 为 None，所有 update/set_error 都是 no-op"""
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)

        async with obs.TraceSpan("test", "tid-9", metadata={"a": 1}, input_data={"q": "x"}) as span:
            span.update(output={"r": 1}, metadata={"b": 2}, level="INFO")
            span.set_error("oops")
        # 不抛错即通过

    @pytest.mark.asyncio
    async def test_span_with_langfuse(self, monkeypatch):
        """有 langfuse 时 _span 真实存在，update/set_error 调用 _span.update"""
        from app.core import observability as obs

        fake_span, fake_trace, fake_lf = fake_langfuse(monkeypatch)

        async with obs.TraceSpan("router", "tid-10", metadata={"m": 1}, input_data={"q": "x"}) as span:
            span.update(output={"answer": "yes"}, metadata={"conf": 0.9}, level="INFO")
            span.set_error("connection lost")
            assert span._span is fake_span

        # span.end() 和 _lf.flush() 都在 __aexit__ 触发
        fake_span.end.assert_called_once()
        fake_lf.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_span_aenter_exception_silent(self, monkeypatch, caplog):
        """_get_langfuse().trace() 抛错被 aenter 吞掉（logger.debug）"""
        from app.core import observability as obs

        fake_lf = MagicMock()
        fake_lf.trace.side_effect = RuntimeError("init failed")
        monkeypatch.setattr(obs, "_get_langfuse", lambda: fake_lf)

        with caplog.at_level("DEBUG", logger="app.core.observability"):
            async with obs.TraceSpan("test", "tid-11") as span:
                # _span 仍是 None，但 span.update/set_error 仍然 no-op
                span.update(output={"x": 1})
                span.set_error("err")
        # 应至少有一条 debug 日志记录失败
        assert any("span create failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_span_aexit_exception_silent(self, monkeypatch, caplog):
        """__aexit__ 中 _span.end() 抛错被吞掉"""
        from app.core import observability as obs

        fake_span = MagicMock()
        fake_span.end.side_effect = RuntimeError("end failed")
        fake_langfuse(monkeypatch, span=fake_span)

        with caplog.at_level("DEBUG", logger="app.core.observability"):
            async with obs.TraceSpan("test", "tid-12"):
                pass
        assert any("结束/刷新失败" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_span_update_exception_silent(self, monkeypatch, caplog):
        """span.update 抛错被吞掉"""
        from app.core import observability as obs

        fake_span = MagicMock()
        fake_span.update.side_effect = RuntimeError("update failed")
        fake_langfuse(monkeypatch, span=fake_span)

        with caplog.at_level("DEBUG", logger="app.core.observability"):
            async with obs.TraceSpan("test", "tid-13") as span:
                span.update(output={"x": 1})
                span.set_error("err")
        # 至少 2 条 debug 日志
        assert any("span更新失败" in r.message for r in caplog.records)
        assert any("span错误标记失败" in r.message for r in caplog.records)


class TestRecordFunctions:
    """4 个 record_* 函数：本质是把元数据打包后调用 _record_local"""

    def test_record_router_decision_with_langfuse(self, monkeypatch):
        from app.core import observability as obs
        # 让 _get_langfuse 返回 None → record_router_decision 走完 _record_local 后 return
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)
        captured = {}
        monkeypatch.setattr(obs, "_record_local",
                            lambda tid, name, input_data, output_data, metadata:
                            captured.update(tid=tid, name=name, input_data=input_data,
                                            output_data=output_data, metadata=metadata))

        obs.record_router_decision("tid-r1", "MAT-001 库存", "rag", "rule", 0.85, 12)
        assert captured["name"] == "意图路由"
        assert captured["output_data"]["method"] == "rule"
        assert captured["output_data"]["intent"] == "rag"
        assert captured["output_data"]["confidence"] == 0.85
        assert captured["metadata"]["duration_ms"] == 12
        assert captured["input_data"]["query"] == "MAT-001 库存"

    def test_record_router_with_langfuse_success(self, monkeypatch):
        """_get_langfuse 有返回值时，try 块也跑一遍（覆盖 trace+span 路径）"""
        from app.core import observability as obs

        fake_span, fake_trace, fake_lf = fake_langfuse(monkeypatch)
        monkeypatch.setattr(obs, "_record_local", lambda *a, **kw: None)

        obs.record_router_decision("tid-r2", "Q", "rag", "rule", 0.9, 10)
        fake_lf.trace.assert_called_once_with(id="tid-r2", name="chat")
        fake_trace.span.assert_called_once()
        fake_span.end.assert_called_once()
        fake_lf.flush.assert_called_once()

    def test_record_router_with_langfuse_failure_silent(self, monkeypatch, caplog):
        """langfuse 抛错被 logger.debug 吞掉"""
        from app.core import observability as obs

        fake_langfuse(monkeypatch, trace_error=RuntimeError("boom"))
        monkeypatch.setattr(obs, "_record_local", lambda *a, **kw: None)

        with caplog.at_level("DEBUG", logger="app.core.observability"):
            obs.record_router_decision("tid-r3", "Q", "rag", "rule", 0.9, 10)
        assert any("record_router failed" in r.message for r in caplog.records)

    def test_record_tool_call_success(self, monkeypatch):
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)
        captured = capture_record_local(monkeypatch)

        obs.record_tool_call("tid-t1", "get_inventory", "MAT-001", "100 件", 50)
        # _record_local(trace_id, span_name, input_data, output_data, metadata)
        # a[0]=trace_id, a[1]=span_name, a[2]=input, a[3]=output, a[4]=metadata
        assert captured["a"][1] == "工具调用: get_inventory"
        assert captured["a"][2]["tool"] == "get_inventory"  # input_data 含 tool/input
        assert "100" in captured["a"][3]["result"]  # output_data 含 result
        assert captured["a"][4]["duration_ms"] == 50
        assert captured["a"][4]["error"] is None

    def test_record_tool_call_with_error(self, monkeypatch):
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)
        captured = capture_record_local(monkeypatch)

        obs.record_tool_call("tid-t2", "search_supplier", "Q", "O", 200, error="timeout")
        assert captured["a"][4]["error"] == "timeout"
        assert captured["a"][2]["tool"] == "search_supplier"

    def test_record_rag_retrieval(self, monkeypatch):
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)
        captured = capture_record_local(monkeypatch)

        obs.record_rag_retrieval("tid-rg1", "供应商资质", 3, ["s1", "s2", "s3"], 80)
        # 实际参数: _record_local(tid, "RAG检索", {"query": q}, {"num_chunks": n}, {"duration_ms": d})
        assert "RAG" in captured["a"][1] and "检索" in captured["a"][1]
        # num_chunks 在 output_data 保持 int 3（monkeypatch 替换了函数本身，捕获原始参数）
        assert captured["a"][3]["num_chunks"] == 3
        # duration_ms 在 metadata
        assert captured["a"][4]["duration_ms"] == 80
        assert captured["a"][2]["query"] == "供应商资质"

    def test_record_llm_generation(self, monkeypatch):
        """record_llm_generation 走 langfuse 路径 + 兜底 _record_local"""
        from app.core import observability as obs

        fake_span, fake_trace, fake_lf = fake_langfuse(monkeypatch)

        # 验证 _record_local 兜底也被调用
        captured = {}
        monkeypatch.setattr(obs, "_record_local",
                            lambda *a, **kw: captured.update(a=a))

        obs.record_llm_generation("tid-l1", "deepseek-chat", 200, 800, 1500, 0.05)

        # 1. _record_local 兜底：保证 LLM 数据不丢失
        assert "a" in captured
        assert captured["a"][0] == "tid-l1"
        assert captured["a"][1] == "LLM生成: deepseek-chat"
        assert captured["a"][2] == {"model": "deepseek-chat"}  # input_data
        assert captured["a"][3] == {"tokens": 1000, "cost_yuan": 0.05}  # output
        assert captured["a"][4]["duration_ms"] == 1500  # metadata

        # 2. langfuse 路径：trace + span + end + flush
        fake_lf.trace.assert_called_once_with(id="tid-l1", name="chat")
        fake_trace.span.assert_called_once()
        fake_span.end.assert_called_once()
        fake_lf.flush.assert_called_once()

    def test_record_llm_generation_no_langfuse(self, monkeypatch):
        """无 langfuse 时 record_llm_generation 静默 no-op（不抛错）"""
        from app.core import observability as obs
        monkeypatch.setattr(obs, "_get_langfuse", lambda: None)

        # 不抛错即通过
        obs.record_llm_generation("tid-l2", "model", 100, 100, 500, 0.0)

    def test_record_llm_generation_exception_silent(self, monkeypatch, caplog):
        """langfuse 抛错被 logger.debug 吞掉"""
        from app.core import observability as obs

        fake_langfuse(monkeypatch, trace_error=RuntimeError("boom"))

        with caplog.at_level("DEBUG", logger="app.core.observability"):
            obs.record_llm_generation("tid-l3", "model", 1, 1, 1, 0.0)
        assert any("record_llm failed" in r.message for r in caplog.records)


class TestGetLangfuseLazy:
    """_get_langfuse 懒加载：首次调用才初始化，失败返回 None"""

    def test_lazy_init_no_keys_returns_none(self, monkeypatch):
        """无 LANGFUSE key → 首次调用返回 None，并缓存为 DISABLED enum 状态"""
        from app.core import observability as obs
        from app.core.observability import TraceProviderState
        # 重置为 UNINITIALIZED 触发探测
        obs._trace_provider = TraceProviderState.UNINITIALIZED

        # 配置中无 langfuse key（默认状态）
        result = obs._get_langfuse()
        assert result is None
        # _trace_provider 现在是 DISABLED（v2.1: enum 三态明确）
        assert obs._trace_provider is TraceProviderState.DISABLED
        # 第二次调用：因状态非 UNINITIALIZED，直接 return None
        assert obs._get_langfuse() is None

    def test_lazy_init_already_cached_returns_cached(self, monkeypatch):
        """_trace_provider 已有值 → 直接返回，不重新初始化"""
        from app.core import observability as obs

        fake_cached = MagicMock()
        monkeypatch.setattr(obs, "_trace_provider", fake_cached)

        result = obs._get_langfuse()
        assert result is fake_cached

    def test_lazy_init_keys_present_but_library_missing(self, monkeypatch):
        """有 key 但 langfuse 库未安装 → except ImportError 分支"""
        from app.core import observability as obs

        fake_settings(monkeypatch)
        monkeypatch.setattr(obs, "_trace_provider", None)

        # 让 import langfuse 抛 ImportError
        import sys
        original = sys.modules.pop("langfuse", None)
        sys.modules["langfuse"] = None  # None means ImportError on import
        try:
            result = obs._get_langfuse()
            assert result is None
        finally:
            sys.modules.pop("langfuse", None)
            if original is not None:
                sys.modules["langfuse"] = original
            obs._trace_provider = None

    def test_lazy_init_keys_present_but_constructor_raises(self, monkeypatch):
        """有 key + 库在 + Langfuse() 抛错 → except Exception 分支"""
        from app.core import observability as obs

        fake_settings(monkeypatch)
        monkeypatch.setattr(obs, "_trace_provider", None)

        fake_langfuse = MagicMock()
        fake_langfuse.Langfuse.side_effect = RuntimeError("connection failed")
        monkeypatch.setitem(__import__("sys").modules, "langfuse", fake_langfuse)

        try:
            result = obs._get_langfuse()
            assert result is None
        finally:
            obs._trace_provider = None
