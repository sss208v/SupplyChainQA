"""
SupplyChainRAG - 三层记忆体系（用户画像 / 部门记忆 / 企业术语表）
============================================================

【设计说明】
按"用户 / 部门 / 企业"三个作用域组织 Agent 长期记忆，支撑
长期状态保持与上下文连贯性：

- 用户层（UserProfileStore）：跨会话用户画像（偏好/常用术语/关注主题），
  key 按 user_id 隔离，解决"新会话接不上上次上下文"的问题
- 部门层（DeptMemoryStore）：部门动态记忆（历史决策/处理约定），
  读写均做角色校验（admin 或同部门角色），与知识库 security_group
  行级 RBAC 保持同一权限口径
- 企业层（GlossaryStore）：企业级术语表（全局统一口径），
  写入仅 admin，读取公开，作为检索注入的固定上下文

【写入时机】显式写入（用户主动告知/管理员维护）+ 异步提炼
（observe_query 从对话中抽取信号，后台任务不阻塞主链路）
【失效策略】TTL 自然过期 + 条目上限裁剪（cap），防止记忆无限膨胀
【降级策略】Redis 不可用时静默跳过并 logger.warning，不阻塞业务

【关键实现约束】
- 动态引用 app.core.redis_client.redis_manager（模块级属性），
  保证测试 patch.object(redis_client, "redis_manager") 生效
- 所有写操作尽量合并为单次 pipeline，减少往返
- 角色校验失败抛 PermissionError，由 API 层转换为 403
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# 部门角色白名单（与 knowledge.py 的 security_group 口径一致）
from app.models.user import UserRole

VALID_DEPT_ROLES = {r.value for r in UserRole}

# 用户画像 Hash 字段名
_PREF_FIELD = "preferences"
_TERM_FIELD = "terms"
_TOPIC_FIELD = "topics"
_UPDATED_FIELD = "updated_at"

# 领域术语白名单（供 observe_query 从对话中轻量提取，不调 LLM）
_DOMAIN_TERMS = (
    "采购", "审批", "物料", "库存", "供应商", "质检", "工单",
    "审批流", "入库", "出库", "物流", "生产计划", "成本核算",
    "采购订单", "供应商准入", "库存周转", "质检标准", "工单流转",
    "ABC分类", "物料编码", "风险评估", "跨部门协作",
)


class UserProfileStore:
    """用户层记忆 — 跨会话用户画像（Redis Hash: scqa:profile:{user_id}）"""

    def __init__(self, manager):
        self.manager = manager

    @staticmethod
    def _key(user_id: str) -> str:
        return f"scqa:profile:{user_id}"

    @staticmethod
    def _load_list(raw: str | None) -> list:
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []

    async def _append_field(self, user_id: str, field: str, value: str) -> None:
        """追加单个条目：去重（忽略大小写）+ cap 裁剪 + TTL，合并为一次 pipeline"""
        if not value or not value.strip():
            return
        value = value.strip()
        if not await self.manager.ensure_connected():
            logger.warning("[UserProfile] Redis不可用，跳过画像写入")
            return
        client = self.manager.client
        key = self._key(user_id)
        raw = await client.hget(key, field)
        items = self._load_list(raw)
        # 去重：相同内容（忽略大小写）不重复记录
        if any(item.lower() == value.lower() for item in items):
            return
        items.append(value)
        max_items = settings.PROFILE_MAX_ITEMS
        if len(items) > max_items:
            items = items[-max_items:]
        pipe = client.pipeline(transaction=True)
        pipe.hset(key, field, json.dumps(items, ensure_ascii=False))
        pipe.hset(
            key, _UPDATED_FIELD,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        pipe.expire(key, settings.PROFILE_TTL)
        await pipe.execute()

    async def add_preference(self, user_id: str, text: str) -> None:
        """记录用户偏好（如"偏好简洁回答"）"""
        await self._append_field(user_id, _PREF_FIELD, text)

    async def add_term(self, user_id: str, term: str) -> None:
        """记录用户常用术语"""
        await self._append_field(user_id, _TERM_FIELD, term)

    async def add_topic(self, user_id: str, topic: str) -> None:
        """记录用户关注主题"""
        await self._append_field(user_id, _TOPIC_FIELD, topic)

    async def get_profile(self, user_id: str) -> dict:
        """读取用户画像原始数据（三类列表 + 更新时间）"""
        if not user_id:
            return {"preferences": [], "terms": [], "topics": []}
        if not await self.manager.ensure_connected():
            return {"preferences": [], "terms": [], "topics": []}
        client = self.manager.client
        key = self._key(user_id)
        raw = await client.hgetall(key)
        return {
            "preferences": self._load_list(raw.get(_PREF_FIELD)),
            "terms": self._load_list(raw.get(_TERM_FIELD)),
            "topics": self._load_list(raw.get(_TOPIC_FIELD)),
            "updated_at": raw.get(_UPDATED_FIELD),
        }

    async def get_profile_context(self, user_id: str) -> str:
        """拼装用户背景文本（供 prompt 注入），全空时返回空串"""
        if not user_id:
            return ""
        profile = await self.get_profile(user_id)
        parts = []
        if profile["preferences"]:
            parts.append("偏好：" + "；".join(profile["preferences"]))
        if profile["terms"]:
            parts.append("常用术语：" + "、".join(profile["terms"]))
        if profile["topics"]:
            parts.append("近期关注：" + "、".join(profile["topics"]))
        if not parts:
            return ""
        return "【用户背景】" + "；".join(parts)

    async def clear_profile(self, user_id: str) -> None:
        """清除用户画像"""
        if not user_id or not await self.manager.ensure_connected():
            return
        await self.manager.client.delete(self._key(user_id))


class DeptMemoryStore:
    """部门层记忆 — 部门动态记忆（Redis Hash: scqa:dept:{role}）

    权限规则（与 security_group 同一口径）：
    - admin：可读写任意部门
    - 部门角色：只能读写自己的部门
    - 其他角色（employee/public 等）：无部门记忆访问权
    """

    def __init__(self, manager):
        self.manager = manager

    @staticmethod
    def _key(dept_role: str) -> str:
        return f"scqa:dept:{dept_role}"

    def _check_access(self, user_role: str, dept_role: str) -> None:
        """角色校验：不通过抛 PermissionError"""
        if dept_role not in VALID_DEPT_ROLES:
            raise PermissionError(f"非法部门角色: {dept_role}")
        if user_role == UserRole.ADMIN.value:
            return
        if user_role != dept_role:
            raise PermissionError(
                f"权限不足：角色 {user_role} 无权访问部门 {dept_role} 的记忆"
            )

    async def add_note(
        self, dept_role: str, content: str, author: str, user_role: str
    ) -> None:
        """写入一条部门记忆（历史决策/处理约定）"""
        self._check_access(user_role, dept_role)
        if not content or not content.strip():
            return
        content = content.strip()
        if not await self.manager.ensure_connected():
            logger.warning("[DeptMemory] Redis不可用，跳过部门记忆写入")
            return
        client = self.manager.client
        key = self._key(dept_role)
        raw = await client.hget(key, "notes")
        notes = []
        if raw:
            try:
                notes = json.loads(raw)
            except (ValueError, TypeError):
                notes = []
        entry = {
            "content": content,
            "author": author or "unknown",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # 去重：相同内容不重复记录
        if any(n.get("content") == content for n in notes):
            return
        notes.append(entry)
        max_items = settings.DEPT_MEMORY_MAX
        if len(notes) > max_items:
            notes = notes[-max_items:]
        pipe = client.pipeline(transaction=True)
        pipe.hset(key, "notes", json.dumps(notes, ensure_ascii=False))
        pipe.hset(key, _UPDATED_FIELD, entry["ts"])
        pipe.expire(key, settings.DEPT_MEMORY_TTL)
        await pipe.execute()
        logger.info(
            f"[DeptMemory] {author} 写入部门记忆: {dept_role} "
            f"content={content[:40]}..."
        )

    async def get_notes(self, dept_role: str, user_role: str) -> list:
        """读取部门记忆列表"""
        self._check_access(user_role, dept_role)
        if not await self.manager.ensure_connected():
            return []
        client = self.manager.client
        raw = await client.hget(self._key(dept_role), "notes")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []

    async def get_dept_context(self, dept_role: str, user_role: str) -> str:
        """拼装部门记忆文本（供 prompt 注入），空时返回空串"""
        if user_role == UserRole.ADMIN.value:
            return ""
        notes = await self.get_notes(dept_role, user_role)
        if not notes:
            return ""
        lines = [f"- {n['content']}" for n in notes[-5:]]
        return "【部门记忆】\n" + "\n".join(lines)

    async def clear_dept(self, dept_role: str, user_role: str) -> None:
        """清除部门记忆"""
        self._check_access(user_role, dept_role)
        if not await self.manager.ensure_connected():
            return
        await self.manager.client.delete(self._key(dept_role))


class GlossaryStore:
    """企业层记忆 — 企业术语表（Redis Hash: scqa:glossary）

    权限规则：写入/删除仅 admin；读取公开（作为固定上下文注入）
    """

    def __init__(self, manager):
        self.manager = manager

    _KEY = "scqa:glossary"

    def _check_admin(self, user_role: str) -> None:
        if user_role != UserRole.ADMIN.value:
            raise PermissionError(
                f"权限不足：企业术语表仅管理员可维护，当前角色: {user_role}"
            )

    async def add_term(self, term: str, definition: str, user_role: str) -> None:
        """新增/更新术语条目"""
        self._check_admin(user_role)
        if not term or not term.strip() or not definition or not definition.strip():
            return
        term = term.strip()
        definition = definition.strip()
        if not await self.manager.ensure_connected():
            logger.warning("[Glossary] Redis不可用，跳过术语表写入")
            return
        client = self.manager.client
        count = await client.hlen(self._KEY)
        exists = await client.hexists(self._KEY, term)
        if not exists and count >= settings.GLOSSARY_MAX_TERMS:
            logger.warning(
                f"[Glossary] 术语表已达上限 {settings.GLOSSARY_MAX_TERMS}，拒绝新增: {term}"
            )
            return
        await client.hset(self._KEY, term, definition)
        logger.info(f"[Glossary] 管理员维护术语: {term}")

    async def get_terms(self) -> dict:
        """读取全部术语（term -> definition）"""
        if not await self.manager.ensure_connected():
            return {}
        return await self.manager.client.hgetall(self._KEY)

    async def get_glossary_context(self) -> str:
        """拼装术语表文本（供 prompt 注入），空时返回空串"""
        terms = await self.get_terms()
        if not terms:
            return ""
        lines = [f"- {term}：{definition}" for term, definition in terms.items()]
        return "【企业术语表】\n" + "\n".join(lines)

    async def delete_term(self, term: str, user_role: str) -> None:
        """删除术语条目"""
        self._check_admin(user_role)
        if not await self.manager.ensure_connected():
            return
        await self.manager.client.hdel(self._KEY, term)


class MemoryService:
    """三层记忆统一门面

    提供两件事：
    1. build_memory_context：把三层记忆拼装为 prompt 注入文本
    2. observe_query：从用户消息异步提炼画像信号（后台任务）
    """

    def __init__(self, manager):
        self._manager = manager
        self.profile = UserProfileStore(manager)
        self.dept = DeptMemoryStore(manager)
        self.glossary = GlossaryStore(manager)

    async def build_memory_context(
        self, user_id: str = "", user_role: str = ""
    ) -> str:
        """拼装三层记忆注入文本

        Args:
            user_id: 用户标识（空则跳过用户层）
            user_role: 用户角色（部门角色时注入该部门记忆；admin 跳过部门段）

        Returns:
            三段式记忆文本（每段空则省略），总开关关闭时返回空串
        """
        if not settings.MEMORY_INJECT_ENABLED:
            return ""
        sections = []

        # 用户层：跨会话画像
        if user_id:
            try:
                profile_ctx = await self.profile.get_profile_context(user_id)
                if profile_ctx:
                    sections.append(profile_ctx)
            except Exception as e:
                logger.warning(f"[Memory] 用户画像读取失败（降级）: {e}")

        # 部门层：部门动态记忆（admin 无本部门概念，跳过）
        if user_role and user_role in VALID_DEPT_ROLES and user_role != UserRole.ADMIN.value:
            try:
                dept_ctx = await self.dept.get_dept_context(user_role, user_role)
                if dept_ctx:
                    sections.append(dept_ctx)
            except Exception as e:
                logger.warning(f"[Memory] 部门记忆读取失败（降级）: {e}")

        # 企业层：术语表（公开）
        try:
            glossary_ctx = await self.glossary.get_glossary_context()
            if glossary_ctx:
                sections.append(glossary_ctx)
        except Exception as e:
            logger.warning(f"[Memory] 企业术语表读取失败（降级）: {e}")

        return "\n\n".join(sections)

    async def observe_query(self, user_id: str, query: str) -> None:
        """从用户消息提炼画像信号并写入（轻量规则，不调 LLM）

        供对话链路在保存会话记忆后以 asyncio.create_task 后台调用，
        不阻塞主链路。
        """
        if not user_id or not query or not settings.MEMORY_INJECT_ENABLED:
            return
        signals = extract_profile_signals(query)
        if not any(signals.values()):
            return
        profile = self.profile
        for pref in signals["preferences"]:
            await profile.add_preference(user_id, pref)
        for term in signals["terms"]:
            await profile.add_term(user_id, term)
        for topic in signals["topics"]:
            await profile.add_topic(user_id, topic)


# ---- 信号提取规则（轻量，可单元测试）----

_PREFERENCE_PATTERN = re.compile(
    r"我(?:喜欢|偏好|习惯|希望|倾向于|更(?:想|喜欢))([^，。！？!?\n]{2,20})"
)
_TOPIC_MIN_LEN = 4
_TOPIC_MAX_LEN = 30


def extract_profile_signals(query: str) -> dict:
    """从用户消息中提取画像信号（纯规则，零 LLM 成本）

    返回 {"preferences": [...], "terms": [...], "topics": [...]}，每类最多 3 条。
    """
    signals = {"preferences": [], "terms": [], "topics": []}
    if not query or not query.strip():
        return signals

    # 偏好句式：'我喜欢简洁回答' → '简洁回答'
    for m in _PREFERENCE_PATTERN.finditer(query):
        pref = m.group(1).strip()
        if pref and pref not in signals["preferences"]:
            signals["preferences"].append(pref)
        if len(signals["preferences"]) >= 3:
            break

    # 领域术语命中
    for term in _DOMAIN_TERMS:
        if term in query and term not in signals["terms"]:
            signals["terms"].append(term)
        if len(signals["terms"]) >= 3:
            break

    # 关注主题：问题主干（去掉语气词后取合理长度片段）
    stripped = re.sub(r"^(请问|帮我|我想问|你好|麻烦)", "", query.strip()).strip()
    if _TOPIC_MIN_LEN <= len(stripped) <= _TOPIC_MAX_LEN:
        signals["topics"].append(stripped)
    elif len(stripped) > _TOPIC_MAX_LEN:
        signals["topics"].append(stripped[:_TOPIC_MAX_LEN])

    return signals


# ---- 全局单例 ----

_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService | None:
    """惰性获取三层记忆服务单例（动态绑定当前 redis_manager）

    注意：不能 from ... import redis_manager（模块级绑定会使测试 patch 失效），
    必须通过 app.core.redis_client 模块属性运行时读取。
    """
    global _memory_service
    from app.core import redis_client as rc_mod

    manager = rc_mod.redis_manager
    if manager is None:
        return None
    if _memory_service is None or _memory_service._manager is not manager:
        _memory_service = MemoryService(manager)
    return _memory_service


def reset_memory_service() -> None:
    """重置单例（测试用：换绑新的 mock redis_manager 后调用）"""
    global _memory_service
    _memory_service = None


def schedule_profile_harvest(user_id: str, query: str) -> None:
    """后台异步提炼画像信号（调用方不等待，异常只记日志）"""
    if not user_id or not query:
        return
    service = get_memory_service()
    if service is None:
        return

    async def _run() -> None:
        try:
            await service.observe_query(user_id, query)
        except Exception as e:
            logger.warning(f"[Memory] 画像提炼任务失败: {e}")

    task = asyncio.create_task(_run())

    def _log_result(t: "asyncio.Task") -> None:
        if not t.cancelled() and t.exception():
            logger.warning(f"[Memory] 画像提炼任务异常: {t.exception()}")

    task.add_done_callback(_log_result)
