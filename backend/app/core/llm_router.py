"""
SmartQA Pro - LLM模型路由
支持 DeepSeek API / MiniMax API / Ollama 本地模型
"""
import logging
from typing import Optional, AsyncIterator
from dataclasses import dataclass, field
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from app.config import get_settings
from app.core.retry import retry_async, retry_astream

logger = logging.getLogger(__name__)
settings = get_settings()


# ---- 各模型定价（每百万token，单位：元）----
MODEL_PRICING = {
    # DeepSeek
    "deepseek-chat":       {"input": 1.0, "output": 2.0},
    "deepseek-coder":      {"input": 1.0, "output": 2.0},
    "deepseek-reasoner":   {"input": 4.0, "output": 16.0},
    "deepseek-v4-flash":   {"input": 0.5, "output": 1.0},   # V4 Flash 最便宜
    "deepseek-v4-pro":     {"input": 2.0, "output": 8.0},
    # MiniMax
    "MiniMax-M2.7":        {"input": 1.0, "output": 8.0},
    "abab6.5-chat":        {"input": 1.0, "output": 8.0},
    # Ollama 本地模型免费
}


@dataclass
class TokenUsage:
    """单次请求的Token用量统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_yuan: float = 0.0       # 本次费用（元）
    model: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_yuan": round(self.cost_yuan, 4),
            "model": self.model,
        }

    @classmethod
    def from_usage_metadata(cls, usage: dict, model: str, provider: str) -> "TokenUsage":
        """从LangChain usage_metadata构建"""
        prompt = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
        completion = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
        total = usage.get("total_tokens", 0) or prompt + completion

        # 计算费用
        pricing = MODEL_PRICING.get(model, {})
        input_cost = (prompt / 1_000_000) * pricing.get("input", 0)
        output_cost = (completion / 1_000_000) * pricing.get("output", 0)
        cost = input_cost + output_cost

        return cls(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_yuan=cost,
            model=model,
            provider=provider,
        )


class LLMFactory:
    """LLM模型工厂"""

    _instances: dict[str, BaseChatModel] = {}

    @classmethod
    def get_llm(
        cls,
        provider: Optional[str] = None,
        temperature: float = 0.7,
        streaming: bool = True,
        model: Optional[str] = None,  # "main" | "fast" | None(默认)
    ) -> BaseChatModel:
        """
        获取LLM实例

        Args:
            provider: 模型提供商 (deepseek / minimax / ollama)
            temperature: 温度参数
            streaming: 是否启用流式输出
            model: 模型选择 — "fast" 用快速模型, "main" 用主模型, None=provider默认
        """
        provider = provider or settings.LLM_PROVIDER
        cache_key = f"{provider}_{model or 'default'}_{temperature}_{streaming}"

        if cache_key in cls._instances:
            return cls._instances[cache_key]

        model_name = None

        if provider == "deepseek":
            # 选择模型
            if model == "fast":
                selected_model = settings.DEEPSEEK_FAST_MODEL
            else:
                selected_model = settings.DEEPSEEK_MODEL

            llm = ChatOpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                model=selected_model,
                temperature=temperature,
                streaming=streaming,
                stream_usage=True,
                max_tokens=1024,
                max_retries=3,
            )
            model_name = selected_model

        elif provider == "minimax":
            llm = ChatOpenAI(
                api_key=settings.MINIMAX_API_KEY,
                base_url=settings.MINIMAX_BASE_URL,
                model=settings.MINIMAX_MODEL,
                temperature=temperature,
                streaming=streaming,
                stream_usage=True,
                max_tokens=1024,
                max_retries=3,
            )
            model_name = settings.MINIMAX_MODEL

        elif provider == "ollama":
            llm = ChatOllama(
                base_url=settings.OLLAMA_BASE_URL,
                model=settings.OLLAMA_MODEL,
                temperature=temperature,
            )
            model_name = settings.OLLAMA_MODEL

        else:
            raise ValueError(f"不支持的LLM提供商: {provider}，可选：deepseek / minimax / ollama")

        cls._instances[cache_key] = llm
        logger.info(f"LLM实例创建: provider={provider}, model={model_name}")
        return llm

    @classmethod
    @retry_async(max_attempts=3, base_delay=2.0)
    async def ainvoke(
        cls,
        messages: list[BaseMessage],
        provider: Optional[str] = None,
        temperature: float = 0.7,
    ) -> tuple[BaseMessage, TokenUsage]:
        """异步调用LLM，返回(response, token_usage)，失败时指数退避重试"""
        llm = cls.get_llm(provider, temperature, streaming=False)
        response = await llm.ainvoke(messages)
        provider_name = provider or settings.LLM_PROVIDER
        model_name = cls._get_model_name(provider_name)
        usage = cls._extract_token_usage(response, model_name, provider_name)
        return response, usage

    @classmethod
    async def _raw_astream(
        cls,
        messages: list[BaseMessage],
        provider: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator:
        """[内部] 异步流式调用LLM（无 retry，由 astream 包装）"""
        llm = cls.get_llm(provider, temperature, streaming=True)
        provider_name = provider or settings.LLM_PROVIDER
        model_name = cls._get_model_name(provider_name)
        last_chunk = None
        async for chunk in llm.astream(messages):
            last_chunk = chunk
            yield chunk
        # 流结束后，从最后一个chunk提取token用量
        if last_chunk is not None:
            usage = cls._extract_token_usage(last_chunk, model_name, provider_name)
            last_chunk._token_usage = usage

    @classmethod
    async def astream(
        cls,
        messages: list[BaseMessage],
        provider: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator:
        """异步流式调用LLM，pre-first-chunk 指数退避重试

        在第一个 token 到达之前，如果网络异常会自动重试（最多3次，2s→4s→8s）。
        如果第一个 token 已发出，后续异常不重试——避免前端收到重复内容。
        """
        async for chunk in retry_astream(
            lambda: cls._raw_astream(messages, provider, temperature),
            max_attempts=3,
            base_delay=2.0,
            context_name="LLM astream",
        ):
            yield chunk

    @classmethod
    def _get_model_name(cls, provider: str) -> str:
        """获取模型名称"""
        if provider == "deepseek":
            return settings.DEEPSEEK_MODEL
        elif provider == "minimax":
            return settings.MINIMAX_MODEL
        elif provider == "ollama":
            return settings.OLLAMA_MODEL
        return "unknown"

    @classmethod
    def _extract_token_usage(cls, response, model: str, provider: str) -> TokenUsage:
        """从LangChain响应中提取token用量"""
        usage = TokenUsage(model=model, provider=provider)
        # LangChain v0.2+ 的 usage_metadata
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage = TokenUsage.from_usage_metadata(response.usage_metadata, model, provider)
        # 旧版 response_metadata
        elif hasattr(response, 'response_metadata') and response.response_metadata:
            meta = response.response_metadata
            if 'token_usage' in meta:
                usage = TokenUsage.from_usage_metadata(meta['token_usage'], model, provider)
        # OpenAI 兼容格式: chunk.usage
        if usage.total_tokens == 0 and hasattr(response, 'usage') and response.usage:
            u = response.usage
            usage = TokenUsage(
                prompt_tokens=getattr(u, 'prompt_tokens', 0) or 0,
                completion_tokens=getattr(u, 'completion_tokens', 0) or 0,
                total_tokens=getattr(u, 'total_tokens', 0) or 0,
                model=model,
                provider=provider,
            )
            pricing = MODEL_PRICING.get(model, {})
            usage.cost_yuan = (usage.prompt_tokens / 1_000_000) * pricing.get("input", 0) + (usage.completion_tokens / 1_000_000) * pricing.get("output", 0)
        return usage
