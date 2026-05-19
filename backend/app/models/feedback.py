"""
SmartQA Pro - 用户反馈数据模型
============================================================
【设计说明】
记录用户对RAG回答的反馈，用于：
- 持续优化回答质量（正面/负面反馈驱动检索策略调整）
- 识别知识库盲点（负面反馈关联的query可发现缺失知识）
- 收集RAGAS评估数据（人工标注作为评估基准）

【字段设计】
- rating: 1=正面, -1=负面（二值化设计，降低用户决策负担）
- confidence: RAG系统返回的置信度，用于与用户反馈交叉分析
- intent: 意图分类标签，用于按意图维度分析满意度
============================================================
"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Feedback(Base):
    """
    用户反馈表

    核心设计原则：
    1. rating使用 1/-1 二值化设计，而非1-5星级
       - 降低用户反馈门槛（二选一 vs 五选一）
       - 便于统计分析（满意度 = positive / total）
    2. 保留confidence和intent字段，用于多维度反馈归因分析
    3. client_info存储客户端信息，用于前端行为分析
    """
    __tablename__ = "feedbacks"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 会话信息 - session_id用于追踪同一会话的多次交互
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)

    # 回答信息 - 记录RAG系统返回的完整回答
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # 参考来源: [{"source": "...", "page": 0}]

    # 反馈内容 - rating为核心字段
    # rating: 1=正面(thumbs up), -1=负面(thumbs down)
    # 设计为1/-1而非bool，便于直接参与数值计算（求和=净满意度）
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=True)  # 用户留言（可选）

    # 质量指标（RAGAS相关）- 用于反馈归因分析
    confidence: Mapped[float] = mapped_column(Float, nullable=True)  # RAG回答置信度
    intent: Mapped[str] = mapped_column(String(32), nullable=True)  # 意图分类标签

    # 关联用户（RBAC：记录反馈提交者）
    user_id: Mapped[int] = mapped_column(Integer, nullable=True, index=True)  # 可匿名

    # 元数据
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False
    )
    client_info: Mapped[dict] = mapped_column(JSON, default=dict)  # 浏览器UA、IP等

    def __repr__(self):
        return f"<Feedback(id={self.id}, rating={self.rating}, session={self.session_id[:8]})>"
