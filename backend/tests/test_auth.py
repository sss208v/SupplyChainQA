"""认证模块单元测试"""
import pytest


# 测试密码哈希
class TestPasswordHash:
    """测试密码哈希逻辑"""

    def test_hash_password(self):
        """测试密码哈希生成"""
        from app.core.auth import hash_password
        h = hash_password("test123")
        assert h is not None
        assert len(h) > 0
        assert h != "test123"  # 不是明文

    def test_hash_contains_salt_and_hash(self):
        """哈希应包含盐值和哈希值（PBKDF2格式）"""
        from app.core.auth import hash_password
        h = hash_password("test123")
        # PBKDF2 格式: salt:hash
        assert ":" in h
        salt, hash_val = h.split(":")
        assert len(salt) == 32  # 16字节hex
        assert len(hash_val) == 64  # 32字节hex

    def test_hash_different_passwords(self):
        """不同密码应生成不同哈希"""
        from app.core.auth import hash_password
        h1 = hash_password("test123")
        h2 = hash_password("test456")
        assert h1 != h2


# 测试用户角色
class TestUserRoles:
    """测试用户角色枚举"""

    def test_admin_role(self):
        """测试管理员角色"""
        from app.models.user import UserRole
        assert UserRole.ADMIN.value == "admin"

    def test_department_roles(self):
        """测试部门角色"""
        from app.models.user import UserRole
        departments = ["purchase", "warehouse", "quality", "production", "finance", "logistics"]
        for dept in departments:
            assert hasattr(UserRole, dept.upper())

    def test_role_count(self):
        """测试角色总数"""
        from app.models.user import UserRole
        roles = [r for r in UserRole]
        assert len(roles) == 7  # admin + 6 departments


# 测试权限过滤
class TestPermissionFilter:
    """测试权限过滤逻辑"""

    def test_admin_sees_all(self):
        """管理员应看到所有文档"""
        # 模拟 admin 角色的过滤表达式
        role = "admin"
        if role != "admin":
            expr = f'array_contains(security_group, "{role}")'
        else:
            expr = "id >= 0"
        assert expr == "id >= 0"

    def test_department_filter(self):
        """部门角色应有正确的过滤表达式"""
        role = "purchase"
        if role != "admin":
            expr = f'array_contains(security_group, "{role}")'
        else:
            expr = "id >= 0"
        assert "purchase" in expr
        assert "array_contains" in expr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ══════════════════════════════════════════════════════════
# JWT Token 测试
# ══════════════════════════════════════════════════════════

import time
import jwt
from unittest.mock import patch


class TestJWTToken:
    """测试JWT Token签发和验证（mock Redis）"""

    @pytest.fixture(autouse=True)
    def _mock_redis(self):
        """注入 FakeRedis，避免依赖真实 Redis"""
        from tests.conftest import _FakeRedisManager
        fake = _FakeRedisManager()
        with patch("app.core.redis_client.redis_manager", fake):
            yield

    @pytest.mark.asyncio
    async def test_create_token_returns_valid_jwt(self):
        """验证create_token返回的是有效JWT（可被decode）"""
        from app.core.auth import create_token
        from app.config import get_settings

        settings = get_settings()
        token = await create_token(user_id=42, username="testuser")

        # JWT格式: xxx.yyy.zzz（三段base64）
        assert token.count(".") == 2

        # 应能被解码并验证签名
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        assert payload["user_id"] == 42
        assert payload["username"] == "testuser"
        assert "jti" in payload
        assert "iat" in payload
        assert "exp" in payload

    @pytest.mark.asyncio
    async def test_verify_token_returns_user_dict(self):
        """验证verify_token能正确解码为user dict"""
        from app.core.auth import create_token, verify_token

        token = await create_token(user_id=1, username="admin")
        user = await verify_token(token)

        assert user is not None
        assert user["user_id"] == 1
        assert user["username"] == "admin"

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self):
        """验证过期token被拒绝"""
        from app.core.auth import verify_token
        from app.config import get_settings

        settings = get_settings()
        now = int(time.time())

        # 手动签发一个已过期的token
        payload = {
            "user_id": 1,
            "username": "expired_user",
            "jti": "test-jti-expired",
            "iat": now - 100000,
            "exp": now - 1,  # 1秒前过期
        }
        expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

        user = await verify_token(expired_token)
        assert user is None

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self):
        """验证伪造签名被拒绝"""
        from app.core.auth import verify_token

        # 用错误密钥签发
        fake_token = jwt.encode(
            {"user_id": 1, "username": "hacker", "jti": "x", "iat": 0, "exp": 9999999999},
            "wrong-secret-key",
            algorithm="HS256",
        )

        user = await verify_token(fake_token)
        assert user is None

    @pytest.mark.asyncio
    async def test_logout_blacklists_token(self):
        """验证登出后token被加入黑名单，无法再验证通过"""
        from app.core.auth import create_token, verify_token, delete_token

        token = await create_token(user_id=1, username="admin")

        # 登出前应能验证通过
        user_before = await verify_token(token)
        assert user_before is not None

        # 登出
        await delete_token(token)

        # 登出后应验证失败（在黑名单中）
        user_after = await verify_token(token)
        assert user_after is None

    @pytest.mark.asyncio
    async def test_none_token_returns_none(self):
        """验证空token返回None"""
        from app.core.auth import verify_token

        assert await verify_token("") is None
        assert await verify_token("not.a.jwt") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
