"""
SmartQA Pro - LangGraph Agent Tests

Tests in this module require a running backend with real LLM credentials.
They are disabled by default to keep the CI fast and deterministic.
Run manually with: pytest tests/test_langgraph_agent.py -v --run-live
"""
import pytest

pytest.skip(reason="Live tests require real LLM + running backend services", allow_module_level=True)
