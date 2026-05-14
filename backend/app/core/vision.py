"""
SmartQA Pro - 多模态图片理解（Vision）
============================================================
使用 MiniMax Vision API 对图片进行理解和描述。
支持 OpenAI 兼容格式的 Vision API 调用。

流程:
  用户上传图片 + 提问 → base64 编码 → Vision API 理解 → 文本描述
  → 注入到 RAG pipeline（作为额外 context 增强检索）

支持的 Provider:
  - minimax: MiniMax Vision API（OpenAI 兼容格式）
  - deepseek: DeepSeek Vision API（如果支持）

使用方式:
    from app.core.vision import vision_engine
    desc = await vision_engine.describe(image_data, user_query)
============================================================
"""
import base64
import logging
import httpx
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class VisionEngine:
    """多模态图片理解引擎"""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._enabled = settings.VISION_ENABLED

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self.api_key)

    @property
    def api_key(self) -> str:
        if settings.VISION_PROVIDER == "minimax":
            return settings.MINIMAX_API_KEY
        elif settings.VISION_PROVIDER == "deepseek":
            return settings.DEEPSEEK_API_KEY
        return ""

    @property
    def base_url(self) -> str:
        if settings.VISION_PROVIDER == "minimax":
            return settings.MINIMAX_BASE_URL
        elif settings.VISION_PROVIDER == "deepseek":
            return settings.DEEPSEEK_BASE_URL
        return ""

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def describe(
        self,
        image_base64: str,
        user_query: str = "请详细描述这张图片的内容，包括其中的文字、数据、图表、物体和场景。",
        mime_type: str = "image/jpeg",
    ) -> str:
        """
        对单张图片进行理解

        Args:
            image_base64: 图片的 base64 编码（不含 data:image/xxx;base64, 前缀）
            user_query: 用户提问或描述指令
            mime_type: 图片 MIME 类型

        Returns:
            图片的文字描述

        Raises:
            RuntimeError: Vision 不可用或调用失败
        """
        if not self.enabled:
            raise RuntimeError("Vision 引擎未启用（检查 VISION_ENABLED 和 API Key）")

        data_url = f"data:{mime_type};base64,{image_base64}"

        payload = {
            "model": settings.VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                        {
                            "type": "text",
                            "text": user_query,
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.3,
        }

        client = await self._get_client()
        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

            content = data["choices"][0]["message"]["content"]
            logger.info(
                f"[Vision] 图片理解完成: {len(image_base64)} bytes → "
                f"{len(content)} chars, "
                f"tokens={data.get('usage', {}).get('total_tokens', '?')}"
            )
            return content

        except httpx.HTTPStatusError as e:
            logger.error(f"[Vision] API 错误: {e.response.status_code} - {e.response.text[:300]}")
            raise RuntimeError(f"Vision API 调用失败: HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"[Vision] 调用异常: {e}")
            raise RuntimeError(f"Vision API 调用失败: {e}")

    async def describe_batch(
        self, images: list[tuple[str, str]], user_query: str = ""
    ) -> list[str]:
        """
        批量理解多张图片

        Args:
            images: [(base64_data, mime_type), ...]
            user_query: 用户提问

        Returns:
            每张图片的文字描述列表
        """
        if len(images) > settings.VISION_MAX_IMAGES:
            logger.warning(
                f"[Vision] 图片数量 {len(images)} 超过上限 {settings.VISION_MAX_IMAGES}，"
                f"截断处理"
            )
            images = images[:settings.VISION_MAX_IMAGES]

        default_query = "请详细描述这张图片的内容，包括其中的文字、数据、图表、物体和场景。"
        query = user_query or default_query

        results = []
        for i, (img_data, mime_type) in enumerate(images):
            try:
                desc = await self.describe(img_data, query, mime_type)
                results.append(desc)
                logger.info(f"[Vision] 图片 {i+1}/{len(images)} 处理完成")
            except Exception as e:
                error_msg = f"[图片{i+1}处理失败: {e}]"
                logger.warning(error_msg)
                results.append(error_msg)

        return results

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# 模块级单例
vision_engine = VisionEngine()
