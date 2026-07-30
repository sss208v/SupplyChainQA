"""
SupplyChainRAG - LLM Router Unit Tests

Tests LLMFactory.get_llm() for provider selection, singleton caching,
model override, and error handling.

All external calls are mocked -- no real API keys or network needed.
"""
import sys
import pytest
from unittest.mock import patch, MagicMock

# Mock langchain_ollama before importing the module under test,
# so the import does not fail when the package is not installed.
if "langchain_ollama" not in sys.modules:
    sys.modules["langchain_ollama"] = MagicMock()

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.llm_router import LLMFactory, TokenUsage, MODEL_PRICING


# ---- helpers ----

def _mock_settings(**overrides):
    """Build a mock settings object with sensible defaults."""
    defaults = {
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "test-ds-key",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "DEEPSEEK_FAST_MODEL": "deepseek-chat",
        "MINIMAX_API_KEY": "test-mm-key",
        "MINIMAX_BASE_URL": "https://api.minimax.chat/v1",
        "MINIMAX_MODEL": "MiniMax-M2.7",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "OLLAMA_MODEL": "qwen2.5:7b",
    }
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset LLMFactory._instances before and after each test."""
    original = LLMFactory._instances.copy()
    LLMFactory._instances.clear()
    yield
    LLMFactory._instances.clear()
    LLMFactory._instances.update(original)


# ---- tests ----

class TestGetLLMDeepseek:
    """DeepSeek provider creates a ChatOpenAI instance with correct params."""

    def test_creates_chat_openai(self):
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()
            LLMFactory.get_llm(provider="deepseek")

            MockChat.assert_called_once()
            assert MockChat.call_args.kwargs["api_key"] == "test-ds-key"
            assert MockChat.call_args.kwargs["model"] == "deepseek-chat"
            assert MockChat.call_args.kwargs["temperature"] == 0.7
            assert MockChat.call_args.kwargs["streaming"] is True

    def test_fast_model_selection(self):
        """model='fast' uses DEEPSEEK_FAST_MODEL."""
        s = _mock_settings(DEEPSEEK_FAST_MODEL="deepseek-v4-flash")
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()
            LLMFactory.get_llm(provider="deepseek", model="fast")

            assert MockChat.call_args.kwargs["model"] == "deepseek-v4-flash"


class TestGetLLMMiniMax:
    """MiniMax provider creates a ChatOpenAI with correct config."""

    def test_creates_minimax_instance(self):
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()
            LLMFactory.get_llm(provider="minimax")

            MockChat.assert_called_once()
            assert MockChat.call_args.kwargs["api_key"] == "test-mm-key"
            assert MockChat.call_args.kwargs["model"] == "MiniMax-M2.7"
            assert MockChat.call_args.kwargs["base_url"] == "https://api.minimax.chat/v1"


class TestGetLLMUnknownFallback:
    """Unknown provider raises ValueError."""

    def test_raises_on_unknown_provider(self):
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s):
            with pytest.raises(ValueError, match="不支持的LLM提供商"):
                LLMFactory.get_llm(provider="openai_gpt5")


class TestSingletonCaching:
    """Same config returns the same cached instance."""

    def test_same_key_returns_same_instance(self):
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            sentinel = MagicMock()
            MockChat.return_value = sentinel

            first = LLMFactory.get_llm(provider="deepseek", temperature=0.7, streaming=True)
            second = LLMFactory.get_llm(provider="deepseek", temperature=0.7, streaming=True)

            assert first is second
            assert MockChat.call_count == 1

    def test_different_temp_creates_new_instance(self):
        """Different temperature produces a separate cache entry."""
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            call_n = [0]
            def _fresh(**kw):
                call_n[0] += 1
                return MagicMock(name=f"ChatOpenAI-call{call_n[0]}")
            MockChat.side_effect = _fresh

            a = LLMFactory.get_llm(provider="deepseek", temperature=0.3)
            b = LLMFactory.get_llm(provider="deepseek", temperature=0.9)

            assert a is not b
            assert MockChat.call_count == 2


class TestAPIKeyMissingError:
    """Missing API key is passed through to the LLM constructor gracefully."""

    def test_empty_api_key_still_creates_instance(self):
        """Factory does not validate the key -- it passes it through."""
        s = _mock_settings(DEEPSEEK_API_KEY="")
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()
            llm = LLMFactory.get_llm(provider="deepseek")

            assert llm is not None
            assert MockChat.call_args.kwargs["api_key"] == ""


class TestModelOverride:
    """Model parameter overrides default model selection."""

    def test_main_uses_default_model(self):
        """model=None uses DEEPSEEK_MODEL."""
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            MockChat.return_value = MagicMock()
            LLMFactory.get_llm(provider="deepseek", model=None)

            assert MockChat.call_args.kwargs["model"] == "deepseek-chat"

    def test_model_param_affects_cache_key(self):
        """Different model param => different cache key => different instance."""
        s = _mock_settings()
        with patch("app.core.llm_router.settings", s), \
             patch("app.core.llm_router.ChatOpenAI") as MockChat:
            call_n = [0]
            def _fresh(**kw):
                call_n[0] += 1
                return MagicMock(name=f"ChatOpenAI-call{call_n[0]}")
            MockChat.side_effect = _fresh

            a = LLMFactory.get_llm(provider="deepseek", model="fast")
            b = LLMFactory.get_llm(provider="deepseek", model=None)

            assert a is not b
            assert MockChat.call_count == 2


class TestTokenUsage:
    """TokenUsage dataclass and from_usage_metadata helper."""

    def test_to_dict_roundtrip(self):
        usage = TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_yuan=0.0012,
            model="deepseek-chat",
        )
        d = usage.to_dict()
        assert d["prompt_tokens"] == 100
        assert d["completion_tokens"] == 50
        assert d["cost_yuan"] == 0.0012
        assert d["model"] == "deepseek-chat"

    def test_from_usage_metadata_calculates_cost(self):
        metadata = {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }
        usage = TokenUsage.from_usage_metadata(metadata, "deepseek-chat", "deepseek")
        # deepseek-chat pricing: input=1.0, output=2.0 per million tokens
        assert usage.prompt_tokens == 1_000_000
        assert usage.completion_tokens == 1_000_000
        assert usage.total_tokens == 2_000_000
        assert abs(usage.cost_yuan - 3.0) < 0.001  # 1.0 + 2.0 = 3.0 yuan

    def test_unknown_model_zero_cost(self):
        metadata = {"input_tokens": 100, "output_tokens": 100}
        usage = TokenUsage.from_usage_metadata(metadata, "unknown-model", "unknown")
        assert usage.cost_yuan == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
