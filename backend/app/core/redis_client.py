"""
SupplyChainRAG - Redis缓存与对话记忆管理
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Optional
import redis.asyncio as aioredis
from app.config import get_settings
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisManager:
    """Redis连接管理器（带自动重连与降级保护）"""

    RECONNECT_INTERVAL = 10  # 断线重连节流间隔（秒）

    # 释放锁 Lua 脚本：GET+DEL 原子比较，只释放自己持有的锁
    _RELEASE_LOCK_LUA = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self):
        self._pool: Optional[aioredis.Redis] = None
        self._last_connect_attempt: float = 0.0
        # 用布尔标志而非 asyncio.Lock 防并发重连：
        # asyncio.Lock 首次 await 时绑定 event loop，单例跨测试 loop 复用会报错
        self._reconnecting: bool = False

    async def connect(self):
        """创建Redis连接池"""
        try:
            self._pool = aioredis.from_url(
                settings.REDIS_URL,
                db=settings.REDIS_DB,
                encoding="utf-8",
                decode_responses=True,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL,
                retry_on_timeout=True,
            )
            # 测试连接
            await self._pool.ping()
            logger.info(f"Redis连接成功: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except Exception as e:
            self._pool = None  # 确保 is_connected 返回 False
            logger.error(f"Redis连接失败: {e}")
            raise

    async def disconnect(self):
        """关闭Redis连接池"""
        if self._pool:
            await self._pool.aclose()  # redis-py 5.x：close() 已弃用
            self._pool = None
            logger.info("Redis连接已断开")

    async def ensure_connected(self) -> bool:
        """懒重连：断线状态下按节流间隔尝试恢复，供业务调用前检查。

        Redis 故障时调用方应降级（跳过缓存/记忆）而非报错；
        Redis 恢复后最多 RECONNECT_INTERVAL 秒内自动接回。
        """
        if self._pool is not None:
            return True
        now = time.monotonic()
        if self._reconnecting or now - self._last_connect_attempt < self.RECONNECT_INTERVAL:
            return False
        self._reconnecting = True
        self._last_connect_attempt = now
        try:
            await self.connect()
            logger.info("[Redis] 自动重连成功")
            return True
        except Exception as e:
            logger.warning(f"[Redis] 自动重连失败: {e}")
            return False
        finally:
            self._reconnecting = False

    @property
    def client(self) -> aioredis.Redis:
        """获取Redis客户端"""
        if not self._pool:
            raise RuntimeError("Redis未连接，请先调用connect()")
        return self._pool

    @property
    def is_connected(self) -> bool:
        """检查 Redis 是否已连接"""
        return self._pool is not None

    # ---- 分布式锁 ----

    async def acquire_lock(
        self, key: str, expire: int = 10, retry_times: int = 1
    ) -> Optional[str]:
        """获取 Redis 分布式锁（简化版 Redlock）

        使用 SET NX EX 原子抢占，锁值为随机 token 标识持有者，
        避免锁过期后误删他人的锁。用于敏感写操作的并发控制。

        Args:
            key: 锁键名，建议格式 lock:tool:{tool_name}:{session_id}:{idempotency_key}
            expire: 锁过期时间（秒），默认 10 秒
            retry_times: 抢占失败后重试次数，默认重试 1 次

        Returns:
            成功返回持有者 token，失败返回 None
        """
        token = uuid.uuid4().hex
        for attempt in range(retry_times + 1):
            acquired = await self.client.set(key, token, nx=True, ex=expire)
            if acquired:
                logger.debug(f"[RedisLock] 获取锁成功: {key}")
                return token
            if attempt < retry_times:
                await asyncio.sleep(0.1)
        logger.warning(f"[RedisLock] 获取锁失败: {key}")
        return None

    async def release_lock(self, key: str, token: str) -> bool:
        """释放锁（Lua 保证只删除自己持有的锁）"""
        released = await self.client.eval(self._RELEASE_LOCK_LUA, 1, key, token)
        if not released:
            logger.warning(f"[RedisLock] 锁已易主，跳过释放: {key}")
            return False
        logger.debug(f"[RedisLock] 释放锁: {key}")
        return True

    async def try_begin_idempotent(self, key: str, ttl: Optional[int] = None) -> str:
        """原子抢占幂等键（SET NX 三态，消除检查-执行竞态）

        Returns:
            'acquired'  本请求获得执行权
            'pending'   相同请求正在处理中
            'completed' 已成功执行过，应拦截重复提交
        """
        ttl = ttl or settings.IDEMPOTENT_TTL
        if await self.client.set(key, "pending", nx=True, ex=ttl):
            return "acquired"
        val = await self.client.get(key)
        return "completed" if val == "completed" else "pending"

    async def mark_idempotent(self, key: str, ttl: Optional[int] = None):
        """执行成功后标记幂等键为已完成"""
        await self.client.set(key, "completed", ex=ttl or settings.IDEMPOTENT_TTL)

    async def cancel_idempotent(self, key: str):
        """执行失败时删除 pending 标记，允许用户重试"""
        await self.client.delete(key)


class ChatMemory:
    """对话记忆管理"""

    def __init__(self, redis_manager: RedisManager):
        self.redis = redis_manager

    def _key(self, session_id: str, user_id: str = "") -> str:
        # user_id 空时使用 anon 前缀：匿名会话与登录用户会话在 key 空间上隔离，
        # 避免知道 session_id 即可读写他人历史（登录用户必须同时匹配 user_id）
        if user_id:
            return f"scqa:chat:{user_id}:{session_id}"
        return f"scqa:chat:anon:{session_id}"

    def _summary_key(self, session_id: str, user_id: str = "") -> str:
        if user_id:
            return f"scqa:chat_summary:{user_id}:{session_id}"
        return f"scqa:chat_summary:anon:{session_id}"

    def _count_key(self, session_id: str, user_id: str = "") -> str:
        if user_id:
            return f"scqa:chat_count:{user_id}:{session_id}"
        return f"scqa:chat_count:anon:{session_id}"

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
        user_id: str = "",
    ) -> None:
        """添加一条消息到对话记忆"""
        if not await self.redis.ensure_connected():
            logger.warning("[ChatMemory] Redis不可用，跳过记忆写入")
            return
        client = self.redis.client
        key = self._key(session_id, user_id)
        count_key = self._count_key(session_id, user_id)

        message = {
            "role": role,
            "content": content,
        }
        if metadata:
            message["metadata"] = json.dumps(metadata, ensure_ascii=False)

        # 推入列表 + 只保留最近N轮 + TTL + 计数：合并为单次 pipeline（原 5 次往返）
        max_len = settings.MEMORY_WINDOW * 2
        pipe = client.pipeline(transaction=True)
        pipe.lpush(key, json.dumps(message, ensure_ascii=False))
        pipe.ltrim(key, 0, max_len - 1)
        pipe.expire(key, settings.MEMORY_TTL)
        pipe.incr(count_key)
        pipe.expire(count_key, settings.MEMORY_TTL)
        results = await pipe.execute()
        msg_count = results[3]

        # ---- 摘要触发：每SUMMARY_INTERVAL条消息生成摘要 ----
        if msg_count >= settings.SUMMARY_INTERVAL:
            # 重置计数
            await client.set(count_key, 0, ex=settings.MEMORY_TTL)
            # 后台任务生成摘要，不阻塞用户请求（LLM 调用可能耗时数秒）
            task = asyncio.create_task(
                self._generate_and_save_summary(session_id, user_id)
            )
            task.add_done_callback(self._log_summary_task_result)

    @staticmethod
    def _log_summary_task_result(task: "asyncio.Task") -> None:
        """后台摘要任务完成回调：记录未捕获异常"""
        if not task.cancelled() and task.exception():
            logger.warning(f"后台摘要任务失败: {task.exception()}")

    async def get_messages(
        self, session_id: str, limit: Optional[int] = None, user_id: str = ""
    ) -> list[dict]:
        """获取对话历史"""
        if not await self.redis.ensure_connected():
            logger.warning("[ChatMemory] Redis不可用，返回空历史")
            return []
        client = self.redis.client
        key = self._key(session_id, user_id)

        max_len = limit or settings.MEMORY_WINDOW * 2
        raw_messages = await client.lrange(key, 0, max_len - 1)

        messages = []
        for raw in reversed(raw_messages):  # lrange返回倒序，需要翻转
            msg = json.loads(raw)
            messages.append(msg)

        return messages

    async def get_context_string(self, session_id: str, user_id: str = "") -> str:
        """获取格式化的对话上下文字符串（含摘要+近期对话）"""
        summary = await self.get_summary(session_id, user_id)
        messages = await self.get_messages(session_id, user_id=user_id)

        parts = []
        if summary:
            parts.append(f"【对话摘要】{summary}")
        if messages:
            lines = []
            for msg in messages:
                role = "用户" if msg["role"] == "user" else "助手"
                lines.append(f"{role}: {msg['content']}")
            parts.append("\n".join(lines))

        return "\n\n".join(parts) if parts else ""

    async def _generate_and_save_summary(self, session_id: str, user_id: str = "") -> None:
        """生成对话摘要并保存（被遗忘的历史对话压缩成摘要）"""
        try:
            messages = await self.get_messages(session_id, limit=settings.SUMMARY_TRUNCATE_LEN, user_id=user_id)
            if not messages:
                return

            # 构建待摘要的文本
            lines = []
            for msg in messages:
                role = "用户" if msg["role"] == "user" else "助手"
                lines.append(f"{role}: {msg['content']}")
            conversation_text = "\n".join(lines)

            # 调用LLM生成摘要
            summary_prompt = f"""请将以下对话记录压缩为一段简洁的摘要，保留关键信息和问题背景：

对话记录：
{conversation_text}

请直接输出摘要，不要前缀："""

            from app.core.llm_router import LLMFactory
            llm = LLMFactory.get_llm(temperature=0.3, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content=summary_prompt),
                HumanMessage(content="请生成摘要"),
            ])

            summary = response.content.strip()
            await self.save_summary(session_id, summary, user_id)
            logger.info(f"对话摘要已生成: user={user_id} session={session_id[:8]}...")

        except Exception as e:
            logger.warning(f"生成对话摘要失败: {e}")

    async def save_summary(self, session_id: str, summary: str, user_id: str = "") -> None:
        """保存对话摘要（Token超限时使用）"""
        if not await self.redis.ensure_connected():
            logger.warning("[ChatMemory] Redis不可用，跳过摘要保存")
            return
        client = self.redis.client
        key = self._summary_key(session_id, user_id)
        await client.set(key, summary, ex=settings.MEMORY_TTL)

    async def get_summary(self, session_id: str, user_id: str = "") -> Optional[str]:
        """获取对话摘要"""
        if not await self.redis.ensure_connected():
            return None
        client = self.redis.client
        key = self._summary_key(session_id, user_id)
        return await client.get(key)

    async def clear_session(self, session_id: str, user_id: str = "") -> None:
        """清除会话记忆"""
        if not await self.redis.ensure_connected():
            logger.warning("[ChatMemory] Redis不可用，跳过会话清除")
            return
        client = self.redis.client
        await client.delete(self._key(session_id, user_id))
        await client.delete(self._summary_key(session_id, user_id))

    async def get_session_list(self) -> list[str]:
        """获取所有活跃会话列表（SCAN 游标迭代，避免 KEYS 阻塞）"""
        if not await self.redis.ensure_connected():
            return []
        client = self.redis.client
        sessions = []
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match="scqa:chat:*", count=100)
            for key in keys:
                # 跳过 summary 和 count key
                if ":chat_summary:" in key or ":chat_count:" in key:
                    continue
                # 键可能为 scqa:chat:{session_id} 或 scqa:chat:{user_id}:{session_id}，取最后一段
                session_part = key.replace("scqa:chat:", "", 1)
                session_id = session_part.rsplit(":", 1)[-1]
                if session_id:
                    sessions.append(session_id)
            if cursor == 0:
                break
        return sessions


# 全局单例
redis_manager = RedisManager()
chat_memory: Optional[ChatMemory] = None


async def init_redis():
    """初始化Redis相关组件"""
    global chat_memory
    await redis_manager.connect()
    chat_memory = ChatMemory(redis_manager)
    logger.info("Redis组件初始化完成")


async def close_redis():
    """关闭Redis连接"""
    await redis_manager.disconnect()
