"""
SmartQA Pro - Redis缓存与对话记忆管理
"""
import json
import logging
from typing import Optional
import redis.asyncio as aioredis
from app.config import get_settings
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisManager:
    """Redis连接管理器"""

    def __init__(self):
        self._pool: Optional[aioredis.Redis] = None

    async def connect(self):
        """创建Redis连接池"""
        try:
            self._pool = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
            )
            # 测试连接
            await self._pool.ping()
            logger.info(f"Redis连接成功: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except Exception as e:
            logger.error(f"Redis连接失败: {e}")
            raise

    async def disconnect(self):
        """关闭Redis连接池"""
        if self._pool:
            await self._pool.close()
            logger.info("Redis连接已断开")

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
    ) -> bool:
        """获取 Redis 分布式锁（简化版 Redlock）

        使用 SET NX EX 实现原子抢占，用于敏感写操作的并发控制。

        Args:
            key: 锁键名，建议格式 lock:tool:{tool_name}:{session_id}:{idempotency_key}
            expire: 锁过期时间（秒），默认 10 秒
            retry_times: 抢占失败后重试次数，默认不重试

        Returns:
            True 表示获取锁成功
        """
        for attempt in range(retry_times + 1):
            acquired = await self.client.set(key, "locked", nx=True, ex=expire)
            if acquired:
                logger.debug(f"[RedisLock] 获取锁成功: {key}")
                return True
            if attempt < retry_times:
                import asyncio
                await asyncio.sleep(0.1)
        logger.warning(f"[RedisLock] 获取锁失败: {key}")
        return False

    async def release_lock(self, key: str):
        """释放锁"""
        await self.client.delete(key)
        logger.debug(f"[RedisLock] 释放锁: {key}")

    async def check_idempotent(self, key: str) -> bool:
        """检查幂等键是否已执行

        用于防止重复提交：写操作执行前检查，执行后标记 completed。
        """
        val = await self.client.get(key)
        return val == "completed"

    async def mark_idempotent(self, key: str, ttl: int = 300):
        """标记幂等键为已执行（保留 5 分钟）"""
        await self.client.set(key, "completed", ex=ttl)


class ChatMemory:
    """对话记忆管理"""

    def __init__(self, redis_manager: RedisManager):
        self.redis = redis_manager

    def _key(self, session_id: str, user_id: str = "") -> str:
        if user_id:
            return f"smartqa:chat:{user_id}:{session_id}"
        return f"smartqa:chat:{session_id}"

    def _summary_key(self, session_id: str, user_id: str = "") -> str:
        if user_id:
            return f"smartqa:chat_summary:{user_id}:{session_id}"
        return f"smartqa:chat_summary:{session_id}"

    def _count_key(self, session_id: str, user_id: str = "") -> str:
        if user_id:
            return f"smartqa:chat_count:{user_id}:{session_id}"
        return f"smartqa:chat_count:{session_id}"

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
        user_id: str = "",
    ) -> None:
        """添加一条消息到对话记忆"""
        client = self.redis.client
        key = self._key(session_id, user_id)

        message = {
            "role": role,
            "content": content,
        }
        if metadata:
            message["metadata"] = json.dumps(metadata, ensure_ascii=False)

        # 推入列表
        await client.lpush(key, json.dumps(message, ensure_ascii=False))

        # 只保留最近N轮（2条/轮：用户+助手）
        max_len = settings.MEMORY_WINDOW * 2
        await client.ltrim(key, 0, max_len - 1)

        # 设置过期时间
        await client.expire(key, settings.MEMORY_TTL)

        # ---- 摘要触发：每SUMMARY_INTERVAL条消息生成摘要 ----
        count_key = self._count_key(session_id, user_id)
        msg_count = await client.incr(count_key)
        await client.expire(count_key, settings.MEMORY_TTL)

        if msg_count >= settings.SUMMARY_INTERVAL:
            # 重置计数
            await client.set(count_key, 0, ex=settings.MEMORY_TTL)
            # 生成摘要（异步，不阻塞响应）
            await self._generate_and_save_summary(session_id, user_id)

    async def get_messages(
        self, session_id: str, limit: Optional[int] = None, user_id: str = ""
    ) -> list[dict]:
        """获取对话历史"""
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
        import asyncio
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
        client = self.redis.client
        key = self._summary_key(session_id, user_id)
        await client.set(key, summary, ex=settings.MEMORY_TTL)

    async def get_summary(self, session_id: str, user_id: str = "") -> Optional[str]:
        """获取对话摘要"""
        client = self.redis.client
        key = self._summary_key(session_id, user_id)
        return await client.get(key)

    async def clear_session(self, session_id: str, user_id: str = "") -> None:
        """清除会话记忆"""
        client = self.redis.client
        await client.delete(self._key(session_id, user_id))
        await client.delete(self._summary_key(session_id, user_id))

    async def get_session_list(self) -> list[str]:
        """获取所有活跃会话列表"""
        client = self.redis.client
        # 只匹配对话消息key，排除摘要和计数key
        keys = await client.keys("smartqa:chat:*")
        sessions = []
        for key in keys:
            # 跳过 summary 和 count key
            if ":chat_summary:" in key or ":chat_count:" in key:
                continue
            # smartqa:chat:{session_id}
            session_id = key.replace("smartqa:chat:", "", 1)
            if session_id:
                sessions.append(session_id)
        return sessions


class CacheManager:
    """缓存管理器"""

    def __init__(self, redis_manager: RedisManager):
        self.redis = redis_manager

    def _key(self, prefix: str, identifier: str) -> str:
        return f"smartqa:cache:{prefix}:{identifier}"

    async def get(self, prefix: str, identifier: str) -> Optional[str]:
        """获取缓存"""
        client = self.redis.client
        return await client.get(self._key(prefix, identifier))

    async def set(
        self, prefix: str, identifier: str, value: str, ttl: int = 3600
    ) -> None:
        """设置缓存"""
        client = self.redis.client
        await client.set(self._key(prefix, identifier), value, ex=ttl)

    async def delete(self, prefix: str, identifier: str) -> None:
        """删除缓存"""
        client = self.redis.client
        await client.delete(self._key(prefix, identifier))


# 全局单例
redis_manager = RedisManager()
chat_memory: Optional[ChatMemory] = None
cache_manager: Optional[CacheManager] = None


async def init_redis():
    """初始化Redis相关组件"""
    global chat_memory, cache_manager
    await redis_manager.connect()
    chat_memory = ChatMemory(redis_manager)
    cache_manager = CacheManager(redis_manager)
    logger.info("Redis组件初始化完成")


async def close_redis():
    """关闭Redis连接"""
    await redis_manager.disconnect()
