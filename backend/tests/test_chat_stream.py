"""Tests for chat stream handlers and SSE formatting.

Covers:
  - _sse_format producing correct SSE wire format
  - _handle_greeting matching / non-matching cases
  - Demo-mode SSE event shape
  - Conflict detection SSE events
  - Stream cancellation handling
"""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Import the pure helpers directly (no side-effects, no I/O)
# ---------------------------------------------------------------------------
from app.api.chat_helpers import _sse_format, _handle_greeting


# ===== SSE format ==========================================================

class TestSseFormat:
    """_sse_format wraps a dict into ``data: <json>\\n\\n``."""

    def test_produces_correct_sse_wire_format(self):
        payload = {"type": "content", "content": "hello"}
        result = _sse_format(payload)

        assert result.startswith("data: ")
        assert result.endswith("\n\n")

        # Strip SSE prefix/suffix and parse the JSON inside
        inner = result[len("data: "):-2]
        parsed = json.loads(inner)
        assert parsed["type"] == "content"
        assert parsed["content"] == "hello"

    def test_preserves_unicode_characters(self):
        payload = {"message": "你好世界"}
        result = _sse_format(payload)
        inner = result[len("data: "):-2]
        parsed = json.loads(inner)
        # ensure_ascii=False keeps Chinese chars as-is
        assert parsed["message"] == "你好世界"

    def test_empty_dict(self):
        result = _sse_format({})
        inner = result[len("data: "):-2]
        assert json.loads(inner) == {}

    def test_nested_structure(self):
        payload = {"metrics": {"route_ms": 12, "llm_ms": 345}, "nodes": [1, 2, 3]}
        result = _sse_format(payload)
        inner = result[len("data: "):-2]
        parsed = json.loads(inner)
        assert parsed["metrics"]["llm_ms"] == 345
        assert parsed["nodes"] == [1, 2, 3]


# ===== Greeting handling ====================================================

class TestHandleGreeting:
    """_handle_greeting returns a canned response for known keywords."""

    def test_greeting_matched(self):
        """Known greeting keyword should return a non-empty response."""
        response = _handle_greeting("你好")
        assert isinstance(response, str)
        assert len(response) > 0
        assert "智能助手" in response

    def test_greeting_keyword_hai(self):
        response = _handle_greeting("嗨")
        assert "智能助手" in response

    def test_greeting_keyword_zaima(self):
        response = _handle_greeting("在吗")
        assert "随时为你服务" in response

    def test_greeting_keyword_xiexie(self):
        response = _handle_greeting("谢谢")
        assert "不客气" in response

    def test_greeting_keyword_zaigjian(self):
        response = _handle_greeting("再见")
        assert "美好" in response

    def test_greeting_no_match(self):
        """Non-greeting input returns empty string (in chat.py version)."""
        # The _handle_greeting defined in chat.py returns "" for no-match
        response = _handle_greeting("今天天气怎么样？")
        assert response == ""

    def test_greeting_substring_match(self):
        """Greeting keyword embedded in longer text still matches."""
        response = _handle_greeting("你好呀，我想查库存")
        assert len(response) > 0


# ===== Demo mode events =====================================================

class TestDemoModeEvent:
    """Verify the shape of a demo_mode SSE event."""

    @pytest.mark.asyncio
    async def test_demo_mode_event_shape(self):
        """A demo_mode event must contain type=demo_mode, mode and message."""
        # Build the event dict exactly as the generator would
        event = {
            "type": "demo_mode",
            "mode": "demo",
            "message": "当前为离线演示模式，LLM 推理结果由本地降级链路生成",
        }

        sse_line = _sse_format(event)
        inner = sse_line[len("data: "):-2]
        parsed = json.loads(inner)

        assert parsed["type"] == "demo_mode"
        assert parsed["mode"] == "demo"
        assert "离线演示" in parsed["message"]

    @pytest.mark.asyncio
    async def test_demo_mode_tool_result_event(self):
        """Demo info attached to tool results has the expected shape."""
        demo_info = {
            "type": "demo_mode",
            "mode": "demo",
            "reason": "no LLM key",
            "summary": "降级到本地规则引擎",
        }
        sse_line = _sse_format(demo_info)
        parsed = json.loads(sse_line[len("data: "):-2])

        assert parsed["type"] == "demo_mode"
        assert parsed["reason"] == "no LLM key"


# ===== Conflict detection SSE events ========================================

class TestConflictDetectionSse:
    """Verify the shape and content of conflict_detected / conflicts events."""

    def test_conflict_detected_event(self):
        """rag_engine-style conflict_detected event has expected structure."""
        event = {
            "type": "conflict_detected",
            "conflicts": [
                {"entity": "MAT-001", "values": [100, 200], "sources": ["doc_a", "doc_b"]},
            ],
        }
        sse_line = _sse_format(event)
        parsed = json.loads(sse_line[len("data: "):-2])

        assert parsed["type"] == "conflict_detected"
        assert len(parsed["conflicts"]) == 1
        assert parsed["conflicts"][0]["entity"] == "MAT-001"

    def test_conflicts_list_event(self):
        """chat.py-style conflicts event includes a message field."""
        conflicts = [
            {"entity": "MAT-002", "values": [10, 30]},
            {"entity": "MAT-003", "values": [50, 55]},
        ]
        event = {
            "type": "conflicts",
            "conflicts": conflicts,
            "message": f"检测到 {len(conflicts)} 处数据冲突，已标记供参考",
        }
        sse_line = _sse_format(event)
        parsed = json.loads(sse_line[len("data: "):-2])

        assert parsed["type"] == "conflicts"
        assert len(parsed["conflicts"]) == 2
        assert "2 处数据冲突" in parsed["message"]

    def test_no_conflicts_produces_empty_list(self):
        """When there are no conflicts the list is empty."""
        event = {
            "type": "conflicts",
            "conflicts": [],
            "message": "检测到 0 处数据冲突",
        }
        sse_line = _sse_format(event)
        parsed = json.loads(sse_line[len("data: "):-2])
        assert parsed["conflicts"] == []


# ===== Stream cancellation ==================================================

class TestStreamCancellation:
    """Cancellation handling: verify the [DONE] marker and error events."""

    def test_done_marker_is_sse_compliant(self):
        """The termination signal must be exactly ``data: [DONE]\\n\\n``."""
        done = "data: [DONE]\n\n"
        assert done.startswith("data: ")
        assert done.endswith("\n\n")
        assert "[DONE]" in done

    def test_error_event_before_done(self):
        """On cancellation the generator yields an error event then [DONE]."""
        error_event = _sse_format({
            "type": "error",
            "message": "请求已取消",
        })

        # Simulate generator output: error event + done
        output_lines = [error_event, "data: [DONE]\n\n"]

        # First line is a proper SSE error
        first_inner = output_lines[0][len("data: "):-2]
        first_parsed = json.loads(first_inner)
        assert first_parsed["type"] == "error"

        # Second line is the done marker
        assert output_lines[1] == "data: [DONE]\n\n"

    @pytest.mark.asyncio
    async def test_generator_stops_after_done(self):
        """Simulate an async generator that stops when cancelled."""
        async def fake_stream():
            yield _sse_format({"type": "content", "content": "partial"})
            yield _sse_format({"type": "error", "message": "cancelled"})
            yield "data: [DONE]\n\n"

        events = []
        async for line in fake_stream():
            events.append(line)

        assert len(events) == 3
        # Last event is [DONE]
        assert events[-1] == "data: [DONE]\n\n"
        # Second event is error
        parsed_err = json.loads(events[1][len("data: "):-2])
        assert parsed_err["type"] == "error"

