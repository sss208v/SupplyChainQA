"""
SmartQA Pro - ToolAgent ReAct Loop Unit Tests

Tests ToolAgent core capabilities:
1. ReAct JSON parsing (extract tool calls from LLM output)
2. Max iterations termination (MAX_ITERATIONS = 5)
3. Tool-not-found error handling
4. Final answer extraction (no tool call case)

Uses unittest.mock.patch to replace LLMFactory.get_llm - no real API key needed.
"""
import pytest
import json
import sys
import os
import asyncio
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.tool import ToolAgent


# ---- Mock LLM 辅助 ----

def make_mock_llm(responses):
    """
    Create a Mock LLM that returns specified content in order.
    Each string in responses is used as the return value of one .ainvoke() call.
    If responses are exhausted, continues returning empty (no tool call, ends directly).
    """
    idx = [0]

    async def mock_invoke(messages):
        if idx[0] < len(responses):
            resp = responses[idx[0]]
            idx[0] += 1
        else:
            resp = "这是一个最终回答，不需要工具。"
        return AIMessage(content=resp)

    mock = MagicMock()
    mock.ainvoke = mock_invoke
    return mock


# ---- Test ReAct JSON Parsing ----

class TestToolAgentParse:
    """Test LLM output parsing capability"""

    def test_parse_valid_json_tool_call(self):
        """Standard JSON format should parse correctly"""
        agent = ToolAgent()
        text = '{"thought": "用户想查库存", "action": "query_inventory", "action_input": {"material_code": "MAT-001"}}'
        result = agent._parse_tool_call(text)

        assert result is not None
        assert result["action"] == "query_inventory"
        assert result["action_input"] == {"material_code": "MAT-001"}

    def test_parse_json_with_thought(self):
        """JSON with surrounding text should still parse"""
        agent = ToolAgent()
        text = 'Hello\n\n{"thought": "check MAT-001", "action": "query_inventory", "action_input": {"material_code": "MAT-001"}}\n\nDone.'
        result = agent._parse_tool_call(text)

        assert result is not None
        assert result["action"] == "query_inventory"

    def test_parse_no_json_returns_none(self):
        """No JSON returns None (treated as final answer)"""
        agent = ToolAgent()
        result = agent._parse_tool_call("Final answer, no tool needed.")
        assert result is None

    def test_parse_incomplete_json_returns_none(self):
        """JSON missing action key returns None"""
        agent = ToolAgent()
        result = agent._parse_tool_call('{"thought": "thinking", "action_input": {}}')
        assert result is None

    def test_parse_wrong_action_key_returns_none(self):
        """JSON with wrong key (tool vs action) returns None"""
        agent = ToolAgent()
        result = agent._parse_tool_call('{"thought": "check", "tool": "query_inventory"}')
        assert result is None


# ---- Test Final Answer Extraction ----

class TestToolAgentFinalAnswer:
    """Test final answer extraction from LLM output"""

    def test_extract_plain_answer(self):
        """Plain text should be returned as-is"""
        agent = ToolAgent()
        text = "MAT-001 inventory is 500 units, status OK."
        result = agent._extract_final_answer(text)
        assert result == text

    def test_extract_with_final_answer_prefix(self):
        """'Final Answer:' prefix should be stripped"""
        agent = ToolAgent()
        text = "Final Answer: MAT-001 has 500 units."
        result = agent._extract_final_answer(text)
        assert not result.startswith("Final Answer")

    def test_extract_json_prefix_removed(self):
        """Content after JSON should be extracted"""
        agent = ToolAgent()
        text = '{"action": "query_inventory"}\n\nMAT-001 has 500 units.'
        result = agent._extract_final_answer(text)
        assert "MAT-001" in result


# ---- Test ReAct Loop (mock LLM) ----

class TestToolAgentReActLoop:
    """Test ToolAgent ReAct loop behavior"""

    def test_no_tool_call_direct_answer(self):
        """No-tool call should return direct answer"""
        with patch("app.agents.tool.LLMFactory") as mock_factory:
            mock_factory.get_llm.return_value = make_mock_llm([
                "This is a general supply chain introduction.",
            ])

            agent = ToolAgent()
            result = asyncio.run(agent.run("Introduce supply chain management"))

            assert "answer" in result
            assert result["iterations"] == 1
            assert result["tool_calls"] == []

    def test_single_tool_call(self):
        """Single tool call"""
        with patch("app.agents.tool.LLMFactory") as mock_factory:
            mock_factory.get_llm.return_value = make_mock_llm([
                '{"thought": "check MAT-001", "action": "query_inventory", "action_input": {"material_code": "MAT-001"}}',
                "MAT-001 has 500 units in stock.",
            ])

            agent = ToolAgent()
            result = asyncio.run(agent.run("MAT-001 inventory?", tool_names=["query_inventory"]))

            assert len(result["tool_calls"]) == 1
            assert result["tool_calls"][0]["tool"] == "query_inventory"
            assert result["iterations"] == 2

    def test_double_tool_call(self):
        """Two tool calls (need result to decide next call)"""
        with patch("app.agents.tool.LLMFactory") as mock_factory:
            mock_factory.get_llm.return_value = make_mock_llm([
                '{"thought": "check MAT-001", "action": "query_inventory", "action_input": {"material_code": "MAT-001"}}',
                '{"thought": "not enough, check PO", "action": "query_order", "action_input": {"order_id": "PO-20250601"}}',
                "Based on results, MAT-001 is insufficient, PO-20250601 has procurement planned.",
            ])

            agent = ToolAgent()
            result = asyncio.run(agent.run("MAT-001 stock enough?", tool_names=["query_inventory", "query_order"]))

            assert len(result["tool_calls"]) == 2
            assert result["tool_calls"][0]["tool"] == "query_inventory"
            assert result["tool_calls"][1]["tool"] == "query_order"
            assert result["iterations"] == 3

    def test_max_iterations_termination(self):
        """Should stop at MAX_ITERATIONS=5 even if LLM keeps calling tools"""
        with patch("app.agents.tool.LLMFactory") as mock_factory:
            forever_responses = [
                '{"thought": "call tool", "action": "get_datetime", "action_input": {}}'
                for _ in range(10)
            ]
            mock_factory.get_llm.return_value = make_mock_llm(forever_responses)

            agent = ToolAgent()
            result = asyncio.run(agent.run("What time is it?", tool_names=["get_datetime"]))

            assert result["iterations"] == ToolAgent.MAX_ITERATIONS
            assert len(result["tool_calls"]) == ToolAgent.MAX_ITERATIONS

    def test_tool_not_found_graceful(self):
        """Wrong tool name should record error, not break loop"""
        with patch("app.agents.tool.LLMFactory") as mock_factory:
            mock_factory.get_llm.return_value = make_mock_llm([
                '{"thought": "call nonexistent tool", "action": "nonexistent_tool", "action_input": {}}',
                "Tool not found, I will answer directly.",
            ])

            agent = ToolAgent()
            result = asyncio.run(agent.run("Any question", tool_names=["query_inventory"]))

            assert len(result["tool_calls"]) == 1
            assert result["tool_calls"][0]["tool"] == "nonexistent_tool"
            assert "不存在" in result["tool_calls"][0]["observation"]


# ---- Test Tool Registry ----

class TestToolRegistry:
    """Test tool registry"""

    def test_all_5_tools_registered(self):
        """TOOL_REGISTRY should contain all 6 tools (including query_supplier)"""
        from app.core.tool_engine import TOOL_REGISTRY

        expected = {"query_inventory", "query_order", "create_ticket",
                    "get_datetime", "get_knowledge", "query_supplier"}
        assert set(TOOL_REGISTRY.keys()) == expected

    def test_tools_have_description(self):
        """All tools should have description attribute"""
        from app.core.tool_engine import TOOL_REGISTRY

        for name, tool in TOOL_REGISTRY.items():
            assert hasattr(tool, "description"), f"{name} missing description"
            assert len(tool.description) > 0, f"{name} description is empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
