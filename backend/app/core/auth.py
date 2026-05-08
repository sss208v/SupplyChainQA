"""
SmartQA Pro - 认证与授权核心模块
============================================================
【设计说明】
1. 密码哈希：使用hashlib的PBKDF2-SHA256（标准库，无需额外依赖）
2. Token管理：使用uuid4生成token，Redis存储，简单可靠
3. RBAC装饰器：检查用户角色权限

【安全设计】
- 密码不可逆哈希（PBKDF2 + 随机salt）
- Token有过期时间（24小时）
- Token绑定用户ID，登出时删除即可失效
============================================================
"""
import uuid
import hashlib
import logging
import secrets
from functools import wraps
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Token前缀
TOKEN_PREFIX = "smartqa:token:"
TOKEN_TTL = 86400  # 24小时


def hash_password(password: str) -> str:
    """
    密码哈希（PBKDF2-SHA256 + 随机salt）

    返回格式：salt:hash（hex编码）
    salt = 16字节随机数
    hash = PBKDF2(password, salt, iterations=100000, dklen=32)
    """
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations=100_000,
        dklen=32,
    )
    return f"{salt}:{dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """
    验证密码

    从存储的salt:hash中提取salt，重新计算hash并比较
    """
    try:
        salt_hex, stored_hash_hex = password_hash.split(":", 1)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            iterations=100_000,
            dklen=32,
        )
        return secrets.compare_digest(dk.hex(), stored_hash_hex)
    except (ValueError, AttributeError):
        return False


async def create_token(user_id: int, username: str) -> str:
    """
    创建认证Token（存入Redis）

    Token = uuid4字符串，足够随机且唯一
    Redis key: smartqa:token:{token} -> {user_id, username}
    """
    from app.core.redis_client import redis_manager

    token = str(uuid.uuid4())
    client = redis_manager.client

    # 存储token -> 用户信息
    token_data = f"{user_id}:{username}"
    await client.set(f"{TOKEN_PREFIX}{token}", token_data, ex=TOKEN_TTL)

    logger.info(f"Token创建成功: user_id={user_id}, username={username}")
    return token


async def verify_token(token: str) -> Optional[dict]:
    """
    验证Token

    从Redis查询token对应的用户信息
    返回 {"user_id": int, "username": str} 或 None
    """
    from app.core.redis_client import redis_manager

    client = redis_manager.client
    token_data = await client.get(f"{TOKEN_PREFIX}{token}")

    if not token_data:
        return None

    try:
        user_id_str, username = token_data.split(":", 1)
        return {"user_id": int(user_id_str), "username": username}
    except (ValueError, AttributeError):
        return None


async def delete_token(token: str) -> None:
    """删除Token（用于登出）"""
    from app.core.redis_client import redis_manager
    client = redis_manager.client
    await client.delete(f"{TOKEN_PREFIX}{token}")


# ---- FastAPI依赖注入 ----

security = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    request: Request,
) -> Optional[dict]:
    """
    可选认证：解析Authorization头，返回用户信息或None

    用于不要求登录但需要识别用户的接口（如chat）
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # 去掉 "Bearer " 前缀
    return await verify_token(token)


async def get_current_user_required(
    request: Request,
) -> dict:
    """
    必选认证：解析Authorization头，返回用户信息

    用于要求登录的接口（如knowledge管理）
    未登录返回401
    """
    user = await get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="未登录或Token已过期，请重新登录",
        )
    return user


async def get_current_user_full(request: Request) -> dict:
    """
    获取完整用户信息（包含role）

    从数据库查询用户的role字段，用于RBAC权限检查
    """
    user = await get_current_user_required(request)

    from app.core.database import async_session
    from app.models.user import User
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == user["user_id"])
        )
        db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(status_code=401, detail="用户不存在")

    if not db_user.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    return {
        "user_id": db_user.id,
        "username": db_user.username,
        "role": db_user.role,
        "department": db_user.department,
    }


def require_role(*allowed_roles: str):
    """
    RBAC权限检查装饰器

    用法：
        @require_role(UserRole.ADMIN, UserRole.PURCHASE)
        async def upload_document(...):
            ...

    用法（在路由函数内部调用）：
        user = await get_current_user_full(request)
        check_role(user, [UserRole.ADMIN, UserRole.PURCHASE])
    """
    async def role_checker(request: Request) -> dict:
        user = await get_current_user_full(request)
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"权限不足，需要角色: {', '.join(allowed_roles)}，当前角色: {user['role']}",
            )
        return user

    return role_checker


def check_role(user: dict, allowed_roles: list[str]) -> None:
    """
    直接检查用户角色（非装饰器用法）

    用于在函数内部手动检查权限
    """
    if user["role"] not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"权限不足，需要角色: {', '.join(allowed_roles)}，当前角色: {user['role']}",
        )
