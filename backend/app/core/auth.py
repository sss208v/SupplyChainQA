"""
SupplyChainRAG - 认证与授权核心模块
============================================================
【设计说明】
1. 密码哈希：使用hashlib的PBKDF2-SHA256（标准库，无需额外依赖）
2. Token管理：JWT（HS256签名）签发 + Redis黑名单（登出撤销）
   - JWT 优势：无状态，本地验签名，不查存储即可验证身份
   - Redis黑名单：登出时加入jti，TTL = 令牌剩余有效期
3. RBAC装饰器：检查用户角色权限

【安全设计】
- 密码不可逆哈希（PBKDF2 + 随机salt）
- JWT 自包含过期时间（exp），防止重放
- Redis黑名单支持主动登出
============================================================
"""
import hashlib
import logging
import secrets
import time
import uuid

import jwt
from fastapi import HTTPException, Request

from app.config import get_settings

logger = logging.getLogger(__name__)

# Redis key前缀（黑名单用）
TOKEN_PREFIX = "scqa:token:"
TOKEN_BLACKLIST_PREFIX = "scqa:blacklist:"
TOKEN_TTL = 86400  # 24小时

# 操作级别排序（admin > manager > employee）
# level 与 role（部门维度）正交：level 控操作权限，role 控数据可见范围
LEVEL_RANK = {"admin": 3, "manager": 2, "employee": 1}


def _get_jwt_secret() -> str:
    """获取JWT密钥（从配置中读取）"""
    return get_settings().JWT_SECRET


def _get_jwt_algorithm() -> str:
    """获取JWT签名算法"""
    return get_settings().JWT_ALGORITHM


def _get_jwt_expire_seconds() -> int:
    """获取JWT过期时间（秒）"""
    return get_settings().JWT_EXPIRE_SECONDS


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
    创建JWT认证Token

    签发流程：
    1. 生成唯一jti（用于登出黑名单）
    2. 编码payload：{user_id, username, jti, iat, exp}
    3. 用HS256 + JWT_SECRET签名
    4. Redis存储 token→用户信息（可选，用于在线用户管理）

    Token可通过 jwt.io 解码验证
    """
    from app.core.redis_client import redis_manager

    settings = get_settings()
    now = int(time.time())
    jti = str(uuid.uuid4())

    payload = {
        "user_id": user_id,
        "username": username,
        "jti": jti,
        "iat": now,
        "exp": now + settings.JWT_EXPIRE_SECONDS,
    }

    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    # 同步存Redis（用于在线状态管理 + 登出黑名单对照）
    try:
        client = redis_manager.client
        if client:
            token_data = f"{user_id}:{username}:{jti}"
            await client.set(
                f"{TOKEN_PREFIX}{jti}", token_data, ex=settings.JWT_EXPIRE_SECONDS
            )
    except Exception as e:
        logger.warning(f"Redis存储token元数据失败（不影响JWT签发）: {e}")

    logger.info(f"JWT签发成功: user_id={user_id}, username={username}")
    return token


async def verify_token(token: str) -> dict | None:
    """
    验证JWT Token

    流程：
    1. jwt.decode()：验证签名 + 过期时间（纯本地运算，不查Redis）
    2. 查Redis黑名单：检查是否已被登出撤销
    3. 返回 {"user_id": int, "username": str} 或 None
    """
    settings = get_settings()

    # 1. JWT解码 + 签名验证（纯本地）
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "jti", "user_id", "username"]},
        )
    except jwt.ExpiredSignatureError:
        logger.debug("JWT验证失败: token已过期")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT验证失败: 签名无效或格式错误 ({e})")
        return None

    # 2. 检查Redis黑名单（登出撤销）
    try:
        from app.core.redis_client import redis_manager

        client = redis_manager.client
        if client:
            blacklisted = await client.get(f"{TOKEN_BLACKLIST_PREFIX}{payload['jti']}")
            if blacklisted:
                logger.debug(f"JWT验证失败: token已被登出撤销 jti={payload['jti']}")
                return None
    except Exception as e:
        logger.warning(f"Redis黑名单查询失败（不影响验证）: {e}")

    return {
        "user_id": payload["user_id"],
        "username": payload["username"],
    }


async def delete_token(token: str) -> None:
    """
    登出Token（加入Redis黑名单）

    流程：
    1. 解码JWT获取jti和剩余有效期（不验证过期，刚过期的也可接受）
    2. 将jti加入Redis黑名单，TTL=剩余有效期
    3. 删除Redis中的活跃token记录
    """
    from app.core.redis_client import redis_manager

    settings = get_settings()

    # 解码获取jti（不验证过期，因为登出时token可能刚刚过期）
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        jti = payload.get("jti", "")
    except jwt.InvalidTokenError:
        logger.debug("登出时JWT解码失败，跳过黑名单")
        return

    client = redis_manager.client
    if not client:
        return

    # 计算剩余有效期
    exp = payload.get("exp", 0)
    remaining_ttl = max(0, exp - int(time.time()))

    # 加入黑名单（TTL = 剩余有效期，过期后自动清除）
    if jti and remaining_ttl > 0:
        await client.set(
            f"{TOKEN_BLACKLIST_PREFIX}{jti}", "1", ex=remaining_ttl
        )

    # 删除活跃token记录
    await client.delete(f"{TOKEN_PREFIX}{jti}")
    logger.info(f"Token已登出: jti={jti}")


# ---- FastAPI依赖注入 ----

security = None  # 不再需要HTTPBearer，使用手动解析


async def _extract_token(request: Request) -> str | None:
    """从Authorization头提取Bearer token"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


async def get_current_user_optional(
    request: Request,
) -> dict | None:
    """
    可选认证：解析Authorization头，返回用户信息或None

    用于不要求登录但需要识别用户的接口（如chat）
    """
    token = await _extract_token(request)
    if not token:
        return None
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

    from sqlalchemy import select

    from app.core.database import async_session
    from app.models.user import User

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
        "level": db_user.level,
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


def check_level(user: dict, min_level: str) -> None:
    """
    检查用户操作级别是否达到最低要求（admin > manager > employee）

    与 check_role 互补：check_role 按部门角色（数据范围）校验，
    check_level 按操作级别（管理权限）校验，两者叠加构成
    “部门 × 级别”二维 RBAC。

    Args:
        user: get_current_user_full 返回的用户 dict
        min_level: 最低要求级别（admin/manager/employee）
    """
    # admin 角色天然拥有全部级别权限（兼容旧数据：role=admin 无 level 字段）
    if user.get("role") == "admin":
        return
    current = user.get("level", "employee")
    if LEVEL_RANK.get(current, 1) < LEVEL_RANK.get(min_level, 1):
        raise HTTPException(
            status_code=403,
            detail=f"权限不足，需要级别: {min_level}，当前级别: {current}",
        )


def require_level(*allowed_levels: str):
    """
    操作级别检查装饰器（非装饰器用法用 check_level）

    用法：
        @require_level(UserLevel.MANAGER, UserLevel.ADMIN)
        async def upload_document(...):
            ...
    """
    async def level_checker(request: Request) -> dict:
        user = await get_current_user_full(request)
        # admin 角色天然拥有全部级别权限
        if user.get("role") == "admin":
            return user
        current = user.get("level", "employee")
        if current not in allowed_levels:
            raise HTTPException(
                status_code=403,
                detail=f"权限不足，需要级别: {', '.join(allowed_levels)}，当前级别: {current}",
            )
        return user

    return level_checker
