"""
SupplyChainRAG - 用户反馈API路由
============================================================
【功能说明】
记录用户对RAG回答的反馈，用于：
1. 持续优化回答质量
2. 识别知识库盲点
3. 为RAGAS提供人工标注数据

【API接口】
- POST /feedback      - 提交反馈
- GET  /feedback/stats - 获取反馈统计分析

【设计决策】
- rating采用 1/-1 二值化设计，简化用户决策
- stats接口返回recent_negative，便于快速定位问题
- 支持按天数筛选统计周期，默认30天
============================================================
"""
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.feedback import Feedback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["反馈"])


# ============================================================
# Pydantic 请求/响应模型
# ============================================================

class FeedbackCreate(BaseModel):
    """
    创建反馈请求体

    rating: 1=正面反馈, -1=负面反馈
    采用二值化设计，降低用户反馈门槛
    """
    session_id: str = Field(..., min_length=1, max_length=64, description="会话ID")
    query: str = Field(..., min_length=1, description="用户问题")
    answer: str = Field(..., min_length=1, description="系统回答")
    sources: list[dict] = Field(default_factory=list, description="参考来源")
    rating: int = Field(..., ge=-1, le=1, description="反馈评分: 1=正面, -1=负面")
    comment: str = Field(None, max_length=500, description="用户留言（可选）")
    confidence: float = Field(None, ge=0, le=1, description="回答置信度（可选）")
    intent: str = Field(None, max_length=32, description="意图类型（可选）")


class NegativeFeedbackItem(BaseModel):
    """
    负面反馈条目

    用于stats接口返回最近的负面反馈详情，
    帮助开发者快速定位需要改进的问题
    """
    query: str
    comment: str | None
    created_at: datetime


class FeedbackStats(BaseModel):
    """
    反馈统计响应

    设计说明：
    - total_feedback: 反馈总数
    - positive_count / negative_count: 正面/负面反馈数量
    - satisfaction_rate: 满意度 = positive / total（0-1之间）
    - recent_negative: 最近5条负面反馈，用于快速定位问题
    """
    total_feedback: int
    positive_count: int
    negative_count: int
    satisfaction_rate: float
    recent_negative: list[NegativeFeedbackItem]


class FeedbackResponse(BaseModel):
    """反馈提交响应"""
    id: int
    rating: int
    created_at: datetime


# ============================================================
# API 端点
# ============================================================

@router.post("", response_model=FeedbackResponse)
async def create_feedback(
    feedback: FeedbackCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    提交用户反馈（需认证）

    前端在用户点击赞/踩按钮时调用此接口。
    数据流: 前端 -> POST /feedback -> 存入PostgreSQL -> 用于后续分析
    """
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)

    # 参数校验：rating必须是1或-1
    if feedback.rating not in (1, -1):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="rating必须为1（正面）或-1（负面）")

    fb = Feedback(
        session_id=feedback.session_id,
        query=feedback.query,
        answer=feedback.answer,
        sources=feedback.sources,
        rating=feedback.rating,
        comment=feedback.comment,
        confidence=feedback.confidence,
        intent=feedback.intent,
        client_info={
            "user_agent": request.headers.get("user-agent", ""),
            "ip": request.client.host if request.client else "",
        },
    )

    db.add(fb)
    await db.flush()
    await db.refresh(fb)

    logger.info(
        f"收到反馈: id={fb.id}, rating={fb.rating}, "
        f"session={fb.session_id[:8]}..."
    )

    return FeedbackResponse(
        id=fb.id,
        rating=fb.rating,
        created_at=fb.created_at,
    )


@router.get("/stats", response_model=FeedbackStats)
async def get_feedback_stats(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="统计周期（天）"),
    db: AsyncSession = Depends(get_db),
):
    """获取反馈统计分析（需认证）"""
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # ============================================================
    # 1. 聚合查询：总数和正面反馈数
    # ============================================================
    # 使用CASE WHEN统计正面反馈，比Python层面过滤更高效
    stats_query = select(
        func.count(Feedback.id).label("total"),
        func.sum(
            func.cast(Feedback.rating == 1, Integer)
        ).label("positive_count"),
    ).where(Feedback.created_at >= since)

    result = await db.execute(stats_query)
    row = result.one()

    total_feedback = row.total or 0
    positive_count = int(row.positive_count or 0)
    negative_count = total_feedback - positive_count

    # 满意度计算：避免除零错误
    satisfaction_rate = (
        round(positive_count / total_feedback, 3) if total_feedback > 0 else 0.0
    )

    # ============================================================
    # 2. 查询最近5条负面反馈详情
    # ============================================================
    # 设计目的：帮助开发者快速定位用户不满意的问题
    negative_query = (
        select(
            Feedback.query,
            Feedback.comment,
            Feedback.created_at,
        )
        .where(Feedback.rating == -1, Feedback.created_at >= since)
        .order_by(Feedback.created_at.desc())
        .limit(5)
    )

    neg_result = await db.execute(negative_query)
    recent_negative = [
        NegativeFeedbackItem(
            query=row.query,
            comment=row.comment,
            created_at=row.created_at,
        )
        for row in neg_result.all()
    ]

    logger.info(
        f"反馈统计: total={total_feedback}, "
        f"positive={positive_count}, negative={negative_count}, "
        f"rate={satisfaction_rate}"
    )

    return FeedbackStats(
        total_feedback=total_feedback,
        positive_count=positive_count,
        negative_count=negative_count,
        satisfaction_rate=satisfaction_rate,
        recent_negative=recent_negative,
    )
