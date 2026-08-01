"""测试配置 — API 集成测试 fixtures"""
import os
import sys
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 测试环境固定 JWT 密钥：未配置时 PyJWT 对空密钥报 InvalidKeyError，
# 必须在任何 app 模块导入（触发 get_settings 缓存）之前设置；长度 >=32 字节避免 HMAC 告警
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only-0123456789")
# 对话接口强制认证默认关闭（大量存量测试匿名调用 /chat/*），
# 认证边界专项测试通过 monkeypatch 单独开启 REQUIRE_AUTH_CHAT
os.environ.setdefault("REQUIRE_AUTH_CHAT", "false")

# 收集阶段忽略非测试目录（pytest 9 用 collect_ignore_glob 替代 ini 的 collect_ignore）
collect_ignore_glob = [
    "_archive/*",
    "_legacy/*",
    "eval/*",
    "eval/_archive/*",
]

# 预导入所有需要 patch 的模块，确保模块级绑定已建立
import app.api.auth
import app.core.database
import app.core.redis_client

# ---- 集成测试：真实服务连接 fixture ----

@pytest_asyncio.fixture
async def _integration_connections():
    """连接真实 Neo4j / Redis（如果可用），每个测试独立连接以避免事件循环冲突。"""
    from app.core.neo4j_client import neo4j_client
    from app.core.redis_client import redis_manager

    connected_neo4j = False
    connected_redis = False
    try:
        await neo4j_client.connect()
        connected_neo4j = True
    except Exception:
        pass
    try:
        await redis_manager.connect()
        connected_redis = True
    except Exception:
        pass

    yield {"neo4j": connected_neo4j, "redis": connected_redis}

    # teardown
    if connected_neo4j:
        try:
            await neo4j_client.disconnect()
        except Exception:
            pass
    if connected_redis:
        try:
            await redis_manager.disconnect()
        except Exception:
            pass


# ---- Fake Redis ----

class _FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}
        self._lists: dict[str, list] = {}
        self._hashes: dict[str, dict] = {}

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None, nx=None, **_kw):
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self._store[key] = value
        return True

    async def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._lists.pop(k, None)
            self._hashes.pop(k, None)
        return len(keys)

    async def incr(self, key):
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key, ttl):
        return True

    async def lpush(self, key, *values):
        lst = self._lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    async def ltrim(self, key, start, end):
        lst = self._lists.get(key, [])
        self._lists[key] = lst[start:] if end == -1 else lst[start:end + 1]
        return True

    async def lrange(self, key, start, end):
        lst = self._lists.get(key, [])
        return lst[start:] if end == -1 else lst[start:end + 1]

    async def eval(self, script, numkeys, *keys_and_args):
        # 模拟 release_lock 的 Lua：token 匹配才删除
        key, token = keys_and_args[0], keys_and_args[1]
        if self._store.get(key) == token:
            self._store.pop(key)
            return 1
        return 0

    async def scan(self, cursor=0, match=None, count=100):
        import fnmatch
        keys = list(self._store) + list(self._lists) + list(self._hashes)
        if match:
            keys = [k for k in keys if fnmatch.fnmatch(k, match)]
        return 0, keys

    async def info(self, section=None):
        return {}

    async def ping(self):
        return True

    # ---- Hash 操作（三层记忆体系：画像/部门记忆/术语表）----
    async def hset(self, key, field, value):
        self._hashes.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        return self._hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return dict(self._hashes.get(key, {}))

    async def hdel(self, key, *fields):
        h = self._hashes.get(key, {})
        removed = 0
        for f in fields:
            if h.pop(f, None) is not None:
                removed += 1
        return removed

    async def hlen(self, key):
        return len(self._hashes.get(key, {}))

    async def hexists(self, key, field):
        return field in self._hashes.get(key, {})

    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _FakePipeline:
    """记录调用序列，execute 时按序返回模拟结果（支持限流与 ChatMemory 两类 pipeline）。"""

    def __init__(self, store: "_FakeRedis"):
        self._store = store
        self._ops: list[tuple] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return _record

    async def execute(self):
        results = []
        for name, args, kwargs in self._ops:
            if name == "incr":
                results.append(await self._store.incr(args[0]))
            elif name == "lpush":
                results.append(await self._store.lpush(*args))
            elif name == "ltrim":
                results.append(await self._store.ltrim(*args))
            elif name == "hset":
                results.append(await self._store.hset(*args))
            elif name == "zcard":
                results.append(0)
            elif name == "zrange":
                results.append([])
            else:
                results.append(True)
        self._ops = []
        return results


class _FakeRedisManager:
    def __init__(self):
        self._pool = _FakeRedis()

    @property
    def client(self):
        return self._pool

    @property
    def is_connected(self):
        return True

    async def ensure_connected(self):
        return True


@pytest_asyncio.fixture
async def client():
    """每测试独立创建内存 SQLite + mock Redis + mock async_session。"""
    from app.core.database import Base

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    fake = _FakeRedisManager()
    test_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # 按顺序 patch：redis → database → auth（必须在 app.main import 之前）
    with patch.object(app.core.redis_client, "redis_manager", fake), \
         patch.object(app.core.database, "async_session", test_session_maker), \
         patch.object(app.api.auth, "async_session", test_session_maker):

        from app.main import app as _app
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

    await engine.dispose()


@pytest_asyncio.fixture
async def seed_user(client):
    resp = await client.post("/api/v1/auth/register", json={
        "username": "testuser",
        "password": "test123456",
        "department": "测试部",
    })
    assert resp.status_code == 200, f"register failed: {resp.status_code} {resp.text}"
    data = resp.json()
    # 既有测试语义：seed_user 代表“部门经理”（可上传/管理本部门文档）
    # register 默认 level=employee，这里通过 DB 提升为 manager 保持向后兼容
    sm = app.api.auth.async_session
    from sqlalchemy import select

    from app.models.user import User
    async with sm() as session:
        result = await session.execute(
            select(User).where(User.username == data["user"]["username"])
        )
        db_user = result.scalar_one()
        db_user.level = "manager"
        await session.commit()
    data["user"]["level"] = "manager"
    return {"token": data["token"], "user": data["user"]}


@pytest_asyncio.fixture
async def employee_user(client):
    """普通员工：register 后保持默认 employee（无任何管理权限）"""
    resp = await client.post("/api/v1/auth/register", json={
        "username": "testemployee",
        "password": "test123456",
        "department": "测试部",
    })
    assert resp.status_code == 200, f"register failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["user"]["level"] == "employee"
    return {"token": data["token"], "user": data["user"]}


@pytest_asyncio.fixture
async def admin_user(client):
    from app.core.auth import hash_password
    from app.models.user import User, UserLevel, UserRole

    # 用 patched 的 async_session 直接写入
    sm = app.api.auth.async_session
    async with sm() as session:
        user = User(
            username="testadmin",
            password_hash=hash_password("admin123456"),
            role=UserRole.ADMIN.value,
            level=UserLevel.ADMIN.value,
            department="管理部",
        )
        session.add(user)
        await session.commit()

    resp = await client.post("/api/v1/auth/login", json={
        "username": "testadmin",
        "password": "admin123456",
    })
    assert resp.status_code == 200
    return {"token": resp.json()["token"], "user": resp.json()["user"]}
