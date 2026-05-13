"""
SmartQA Pro - 多模态嵌入引擎（CLIP）
============================================================
同时编码文本和图像到统一向量空间，实现跨模态检索。

架构三（混合多模态 RAG）的核心组件：
  - 文本 → CLIP text encoder → 512维向量
  - 图片 → CLIP image encoder → 512维向量
  - 同一空间中：文字可以直接搜图片，图片也可以搜文字

模型：openai/clip-vit-base-patch32（HuggingFace，约400MB）
      Chinese-CLIP 可作为中文场景替代

使用方式：
    from app.core.multimodal_embedding import clip_engine
    img_vec = clip_engine.encode_image(image_bytes)
    txt_vec = clip_engine.encode_text("一张蓝色的图表")
============================================================
"""
import logging
import base64
import io
from typing import Optional
from PIL import Image
import torch
from transformers import CLIPProcessor, CLIPModel

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CLIPEmbeddingEngine:
    """CLIP 多模态嵌入引擎"""

    def __init__(self):
        self._model: Optional[CLIPModel] = None
        self._processor: Optional[CLIPProcessor] = None
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._initialized = False

    @property
    def model_name(self) -> str:
        return getattr(settings, "CLIP_MODEL", "openai/clip-vit-base-patch32")

    @property
    def embedding_dim(self) -> int:
        return 512  # CLIP ViT-B/32 输出维度

    def init(self):
        """懒加载 CLIP 模型（首次调用时自动触发）"""
        if self._initialized:
            return
        logger.info(f"[CLIP] 加载模型: {self.model_name} (device={self._device})")
        self._model = CLIPModel.from_pretrained(self.model_name).to(self._device)
        self._processor = CLIPProcessor.from_pretrained(self.model_name)
        self._model.eval()
        self._initialized = True
        logger.info(f"[CLIP] 模型加载完成，embedding_dim={self.embedding_dim}")

    def encode_text(self, text: str) -> list[float]:
        """将文本编码为 CLIP 向量"""
        self.init()
        with torch.no_grad():
            inputs = self._processor(
                text=[text], return_tensors="pt", padding=True, truncation=True
            ).to(self._device)
            outputs = self._model.get_text_features(**inputs)
            # 兼容不同版本的 CLIP 输出格式
            if hasattr(outputs, "pooler_output"):
                features = outputs.pooler_output
            elif hasattr(outputs, "text_embeds"):
                features = outputs.text_embeds
            else:
                features = outputs
            features = features / features.norm(dim=-1, keepdim=True)
            return features.cpu().numpy()[0].tolist()

    def encode_image(self, image_data: bytes) -> list[float]:
        """将图片（bytes）编码为 CLIP 向量"""
        self.init()
        image = Image.open(io.BytesIO(image_data)).convert("RGB")
        with torch.no_grad():
            inputs = self._processor(
                images=image, return_tensors="pt"
            ).to(self._device)
            outputs = self._model.get_image_features(**inputs)
            if hasattr(outputs, "pooler_output"):
                features = outputs.pooler_output
            elif hasattr(outputs, "image_embeds"):
                features = outputs.image_embeds
            else:
                features = outputs
            features = features / features.norm(dim=-1, keepdim=True)
            return features.cpu().numpy()[0].tolist()

    def encode_image_base64(self, base64_data: str) -> list[float]:
        """将 base64 编码的图片转为 CLIP 向量"""
        raw = base64.b64decode(base64_data)
        return self.encode_image(raw)

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """计算两个 CLIP 向量的余弦相似度"""
        import math
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        na = math.sqrt(sum(a * a for a in vec_a))
        nb = math.sqrt(sum(b * b for b in vec_b))
        return dot / (na * nb) if na > 0 and nb > 0 else 0.0

    @property
    def ready(self) -> bool:
        return self._initialized


# 模块级单例
clip_engine = CLIPEmbeddingEngine()
