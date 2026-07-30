"""
SupplyChainRAG - 工具API路由
============================================================
1. 工具管理API提供：
   - 工具列表查询（按角色权限过滤）
   - 工具测试接口（权限校验）
   - 工具调用状态查询

2. 工具权限说明：
   - 工具按角色分配可见性和可调用性
   - admin 角色拥有全部工具权限
   - 普通角色只能使用其对应部门的工具
============================================================
"""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from app.core.tool_engine import TOOL_REGISTRY
from app.agents.tool import tool_agent
from app.core.auth import get_current_user_full

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["工具"])

# ==========================================
# 工具权限配置
# ==========================================

# 每个角色可调用的工具列表
ROLE_TOOLS = {
    "admin": {
        "query_inventory", "query_order", "create_ticket",
        "get_datetime", "get_knowledge", "query_supplier",
        "track_logistics", "calculate_reorder_point",
    },
    "purchase": {
        "query_inventory", "query_order", "create_ticket",
        "get_datetime", "get_knowledge", "query_supplier",
        "track_logistics", "calculate_reorder_point",
    },
    "warehouse": {
        "query_inventory", "create_ticket",
        "get_datetime", "get_knowledge",
        "track_logistics",
    },
    "quality": {
        "create_ticket", "get_datetime", "get_knowledge",
        "track_logistics",
    },
    "production": {
        "create_ticket", "get_datetime", "get_knowledge",
        "track_logistics", "calculate_reorder_point",
    },
    "finance": {
        "get_datetime", "get_knowledge",
    },
    "logistics": {
        "create_ticket", "get_datetime", "get_knowledge",
        "track_logistics",
    },
}


class ToolCallRequest(BaseModel):
    """工具调用请求"""
    query: str = Field(..., min_length=1, description="用户问题")
    tool_names: Optional[list[str]] = Field(None, description="指定工具名称列表")
    session_id: Optional[str] = Field(None, description="会话ID")


class ToolInfo(BaseModel):
    """工具信息"""
    name: str
    description: str
    allowed_roles: list[str] = []  # 可调用该工具的角色列表


class ToolListResponse(BaseModel):
    """工具列表响应"""
    total: int
    tools: list[ToolInfo]


class ToolCallResponse(BaseModel):
    """工具调用响应"""
    answer: str
    tool_calls: list
    iterations: int


# ---- API接口 ----

def _get_allowed_tools(role: str) -> set[str]:
    """获取角色可调用的工具集合"""
    return ROLE_TOOLS.get(role, ROLE_TOOLS.get("purchase", set()))


def _is_tool_allowed(tool_name: str, role: str) -> bool:
    """检查工具是否对角色开放"""
    if role not in ROLE_TOOLS:
        return False
    return tool_name in ROLE_TOOLS[role]


def _get_tool_allowed_roles(tool_name: str) -> list[str]:
    """获取允许调用某工具的所有角色"""
    roles = []
    for role, tools in ROLE_TOOLS.items():
        if tool_name in tools:
            roles.append(role)
    return roles


@router.get("/list", response_model=ToolListResponse)
async def list_tools(request: Request):
    """获取当前用户可调用的工具列表（按角色权限过滤）"""
    current_user = await get_current_user_full(request)
    allowed_tools = _get_allowed_tools(current_user.get("role", "purchase"))

    tools = []
    for name in allowed_tools:
        if name in TOOL_REGISTRY:
            tool_func = TOOL_REGISTRY[name]
            description = tool_func.description if hasattr(tool_func, 'description') else "无描述"
            tools.append(ToolInfo(
                name=name,
                description=description,
                allowed_roles=_get_tool_allowed_roles(name)
            ))

    return ToolListResponse(total=len(tools), tools=tools)


@router.get("/schema")
async def get_tool_schemas(request: Request):
    """从 TOOL_REGISTRY 动态生成工具输入 Schema（后端为单一事实来源）

    替代前端 Tools/index.vue 中硬编码的 toolSchemas：新增工具只需注册到
    TOOL_REGISTRY，前端自动展示，无需两处同步维护。
    """
    current_user = await get_current_user_full(request)
    allowed_tools = _get_allowed_tools(current_user.get("role", "purchase"))

    schemas = {}
    for name, tool_func in TOOL_REGISTRY.items():
        if name not in allowed_tools:
            continue
        inputs = []
        try:
            args_schema = getattr(tool_func, "args_schema", None)
            if args_schema is not None:
                json_schema = args_schema.model_json_schema()
                required = set(json_schema.get("required", []))
                for pname, prop in json_schema.get("properties", {}).items():
                    inputs.append({
                        "name": pname,
                        "type": prop.get("type", "str"),
                        "description": prop.get("description", "") or prop.get("title", ""),
                        "required": pname in required,
                    })
        except Exception as e:
            logger.warning(f"[Tool] 生成 {name} schema 失败: {e}")
        description = getattr(tool_func, "description", "") or ""
        schemas[name] = {
            "inputs": inputs,
            "description": description.strip(),
            "allowed_roles": _get_tool_allowed_roles(name),
        }

    return {"total": len(schemas), "schemas": schemas}


@router.post("/call", response_model=ToolCallResponse)
async def call_tool(request: Request, body: ToolCallRequest):
    """
    直接调用工具（测试接口）

    与chat接口的区别：跳过意图路由，直接进入工具调用Agent
    适用于前端工具管理页面的"测试工具"功能
    """
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "finance") if current_user else "finance"
    allowed_tools = _get_allowed_tools(user_role)

    # Agent 自动选择工具时，检查 Agent 可能调用的所有工具
    if not body.tool_names:
        # Agent 自由选择——检查该角色可用工具列表是否为空
        if not allowed_tools:
            raise HTTPException(status_code=403, detail="当前角色无可用工具权限")
    else:
        for tool_name in body.tool_names:
            if tool_name not in allowed_tools:
                raise HTTPException(
                    status_code=403,
                    detail=f"无权调用工具: {tool_name}，请联系管理员开通权限"
                )

    result = await tool_agent.run(
        query=body.query,
        tool_names=body.tool_names,
        session_id=body.session_id,
    )

    return ToolCallResponse(
        answer=result["answer"],
        tool_calls=result["tool_calls"],
        iterations=result["iterations"],
    )


@router.get("/{tool_name}/schema")
async def get_tool_schema(tool_name: str, request: Request):
    """获取工具的参数Schema"""
    current_user = await get_current_user_full(request)
    if not _is_tool_allowed(tool_name, current_user.get("role", "purchase")):
        raise HTTPException(status_code=403, detail=f"无权查看工具: {tool_name}")
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"工具不存在: {tool_name}")

    tool_func = TOOL_REGISTRY[tool_name]

    # 从tool装饰器提取参数信息
    schema = {
        "name": tool_name,
        "description": tool_func.description if hasattr(tool_func, 'description') else "",
        "args": {},
    }

    # LangChain的tool对象有args_schema属性
    if hasattr(tool_func, 'args_schema'):
        schema_fn = getattr(tool_func.args_schema, 'model_json_schema', None) or getattr(tool_func.args_schema, 'schema', None)
        if schema_fn:
            schema["args"] = schema_fn()

    return schema
