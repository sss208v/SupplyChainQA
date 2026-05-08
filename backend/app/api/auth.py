"""
SmartQA Pro - 认证API路由
============================================================
1. Token认证 vs JWT：
   - JWT：无状态，自包含，但无法主动失效
   - UUID Token + Redis：有状态，可主动登出，更灵活
   本项目采用UUID Token方案，简单可靠

2. 接口设计：
   - POST /login：用户名密码 → token
   - POST /register：注册新用户
   - POST /logout：登出（删除token）
   - GET /me：获取当前用户信息
============================================================
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.database import async_session
from app.core.auth import (
    hash_password,
    verify_password,
    create_token,
    delete_token,
    get_current_user_required,
)
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])


# ---- 请求/响应模型 ----

class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=4, max_length=128, description="密码")


class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码（至少6位）")
    department: str = Field(None, max_length=64, description="所属部门（可选）")


class TokenResponse(BaseModel):
    """Token响应"""
    token: str
    user: dict


class UserResponse(BaseModel):
    """用户信息响应"""
    id: int
    username: str
    role: str
    department: str | None
    is_active: bool
    created_at: str | None


# ---- API接口 ----

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """
    用户登录

    验证用户名密码，返回认证Token
    Token存储在Redis中，有效期24小时
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.username == body.username)
        )
        user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 创建Token
    token = await create_token(user.id, user.username)

    logger.info(f"用户登录成功: {user.username} (role={user.role})")

    return TokenResponse(
        token=token,
        user=user.to_dict(),
    )


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest):
    """
    用户注册

    新用户默认角色为employee（普通员工）
    注册后自动登录返回Token
    """
    async with async_session() as session:
        # 检查用户名是否已存在
        result = await session.execute(
            select(User).where(User.username == body.username)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="用户名已存在")

        # 创建新用户
        new_user = User(
            username=body.username,
            password_hash=hash_password(body.password),
            role=UserRole.PURCHASE.value,  # 默认采购部
            department=body.department,
        )
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)

    # 创建Token
    token = await create_token(new_user.id, new_user.username)

    logger.info(f"新用户注册成功: {new_user.username}")

    return TokenResponse(
        token=token,
        user=new_user.to_dict(),
    )


@router.post("/logout")
async def logout(request: Request):
    """
    用户登出

    从Redis删除Token
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        await delete_token(token)

    return {"message": "已成功登出"}


@router.get("/me", response_model=UserResponse)
async def get_me(request: Request):
    """
    获取当前登录用户信息

    需要在请求头携带 Authorization: Bearer <token>
    """
    user_data = await get_current_user_required(request)

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == user_data["user_id"])
        )
        user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        department=user.department,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


@router.get("/users")
async def list_users(request: Request):
    """
    获取用户列表（仅管理员可用）
    """
    from app.core.auth import get_current_user_full, check_role

    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value])

    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

    return {
        "total": len(users),
        "users": [u.to_dict() for u in users],
    }
