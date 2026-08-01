"""
SupplyChainRAG - 三层记忆体系管理 API
============================================================

【API接口】
- GET    /memory/profile                 - 读取当前用户画像
- POST   /memory/profile/preferences     - 显式写入用户偏好
- GET    /memory/dept                    - 读取本部门记忆（admin 可指定部门）
- POST   /memory/dept/notes              - 写入本部门记忆（admin 可指定部门）
- GET    /memory/glossary                - 读取企业术语表（登录即可）
- POST   /memory/glossary                - 维护术语条目（仅 admin）
- DELETE /memory/glossary/{term}         - 删除术语条目（仅 admin）

【权限设计】
- 用户画像：仅本人可读写（user_id 取自登录态，不信任前端）
- 部门记忆：非 admin 强制读写自己的部门（与 knowledge.py
  _resolve_security_groups 同一防越权口径），admin 可指定任意部门
- 企业术语表：写入仅 admin，读取公开

【设计决策】
- 记忆管理接口与对话注入解耦：对话链路由 memory_service 自动注入，
  管理接口仅供用户/管理员显式维护，形成"显式写入 + 异步提炼"双通道
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import check_level, get_current_user_full
from app.core.memory_service import get_memory_service
from app.models.user import UserLevel, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory", tags=["三层记忆"])


# ============================================================
# Pydantic 请求模型
# ============================================================

class PreferenceCreate(BaseModel):
    """用户偏好显式写入"""
    preference: str = Field(..., min_length=1, max_length=200, description="用户偏好表述（如：偏好简洁回答）")


class DeptNoteCreate(BaseModel):
    """部门记忆写入"""
    dept_role: str = Field(None, max_length=32, description="部门角色（admin 可指定，其余强制为自身角色）")
    content: str = Field(..., min_length=1, max_length=500, description="部门记忆内容（历史决策/处理约定）")


class GlossaryCreate(BaseModel):
    """企业术语条目维护"""
    term: str = Field(..., min_length=1, max_length=64, description="术语名称")
    definition: str = Field(..., min_length=1, max_length=300, description="术语定义")


def _resolve_dept_role(requested: str, user_role: str) -> str:
    """解析目标部门角色（服务端权威裁决，防越权）

    规则：
    - admin：可指定任意合法部门角色，缺省为 admin
    - 部门角色：强制为自身角色，忽略前端传入值
    - 其他角色：无部门记忆访问权
    """
    from app.core.memory_service import VALID_DEPT_ROLES

    if user_role == UserRole.ADMIN.value:
        dept = requested or UserRole.ADMIN.value
        if dept not in VALID_DEPT_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"非法部门角色: {dept}，允许值: {sorted(VALID_DEPT_ROLES)}",
            )
        return dept
    if user_role not in VALID_DEPT_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"角色 {user_role} 无部门记忆访问权",
        )
    if requested and requested != user_role:
        logger.warning(
            f"[Memory][RBAC] 用户角色={user_role} 请求部门 {requested}，已强制降级为自身角色"
        )
    return user_role


# ============================================================
# 用户层：画像
# ============================================================

@router.get("/profile")
async def get_profile(request: Request):
    """读取当前用户画像（仅本人）"""
    current_user = await get_current_user_full(request)
    user_id = current_user.get("username", "") or str(current_user.get("user_id", ""))
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    profile = await service.profile.get_profile(user_id)
    return {"user_id": user_id, "profile": profile}


@router.post("/profile/preferences")
async def add_preference(body: PreferenceCreate, request: Request):
    """显式写入当前用户偏好"""
    current_user = await get_current_user_full(request)
    user_id = current_user.get("username", "") or str(current_user.get("user_id", ""))
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    await service.profile.add_preference(user_id, body.preference)
    return {"status": "ok", "user_id": user_id, "preference": body.preference}


# ============================================================
# 部门层：部门记忆
# ============================================================

@router.get("/dept")
async def get_dept_memory(request: Request, dept_role: str = ""):
    """读取部门记忆（非 admin 强制自身角色）"""
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "")
    dept = _resolve_dept_role(dept_role, user_role)
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    notes = await service.dept.get_notes(dept, user_role)
    return {"dept_role": dept, "notes": notes}


@router.post("/dept/notes")
async def add_dept_note(body: DeptNoteCreate, request: Request):
    """写入部门记忆（部门经理及以上，非 admin 强制自身角色）"""
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "")
    # 级别校验：仅 manager+ 可沉淀部门记忆（employee 只读）
    check_level(current_user, UserLevel.MANAGER.value)
    dept = _resolve_dept_role(body.dept_role, user_role)
    author = current_user.get("username", "") or str(current_user.get("user_id", ""))
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    try:
        await service.dept.add_note(dept, body.content, author, user_role)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "ok", "dept_role": dept, "content": body.content}


# ============================================================
# 企业层：术语表
# ============================================================

@router.get("/glossary")
async def get_glossary(request: Request):
    """读取企业术语表（登录即可，读取公开）"""
    await get_current_user_full(request)
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    terms = await service.glossary.get_terms()
    return {"terms": terms, "count": len(terms)}


@router.post("/glossary")
async def add_glossary(body: GlossaryCreate, request: Request):
    """维护企业术语条目（仅 admin）"""
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "")
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    try:
        await service.glossary.add_term(body.term, body.definition, user_role)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "ok", "term": body.term}


@router.delete("/glossary/{term}")
async def delete_glossary(term: str, request: Request):
    """删除企业术语条目（仅 admin）"""
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "")
    service = get_memory_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Redis 不可用，记忆服务未就绪")
    try:
        await service.glossary.delete_term(term, user_role)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "ok", "term": term}
