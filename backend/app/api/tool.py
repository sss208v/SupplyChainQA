"""
SmartQA Pro - 工具API路由
============================================================
1. 工具管理API提供：
   - 工具列表查询
   - 工具测试接口
   - 工具调用状态查询

2. 在企业级Agent系统中，工具是动态注册的：
   - 运行时可以添加/删除工具
   - 工具需要描述（让LLM知道什么时候该用）
   - 工具需要参数Schema（让LLM知道怎么传参）

3. OpenAI Function Calling的工具定义格式已成为行业标准：
   {
     "name": "get_weather",
     "description": "查询城市天气",
     "parameters": {
       "type": "object",
       "properties": {"city": {"type": "string"}},
       "required": ["city"]
     }
   }
============================================================
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from app.core.tool_engine import TOOL_REGISTRY, get_all_tools
from app.agents.tool import tool_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["工具"])


class ToolCallRequest(BaseModel):
    """工具调用请求"""
    query: str = Field(..., min_length=1, description="用户问题")
    tool_names: Optional[list[str]] = Field(None, description="指定工具名称列表")
    session_id: Optional[str] = Field(None, description="会话ID")


class ToolInfo(BaseModel):
    """工具信息"""
    name: str
    description: str


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

@router.get("/list", response_model=ToolListResponse)
async def list_tools():
    """获取所有已注册的工具列表"""
    tools = []
    for name, tool_func in TOOL_REGISTRY.items():
        description = tool_func.description if hasattr(tool_func, 'description') else "无描述"
        tools.append(ToolInfo(name=name, description=description))

    return ToolListResponse(total=len(tools), tools=tools)


@router.post("/call", response_model=ToolCallResponse)
async def call_tool(request: ToolCallRequest):
    """
    直接调用工具（测试接口）

    与chat接口的区别：跳过意图路由，直接进入工具调用Agent
    适用于前端工具管理页面的"测试工具"功能
    """
    result = await tool_agent.run(
        query=request.query,
        tool_names=request.tool_names,
        session_id=request.session_id,
    )

    return ToolCallResponse(
        answer=result["answer"],
        tool_calls=result["tool_calls"],
        iterations=result["iterations"],
    )


@router.get("/{tool_name}/schema")
async def get_tool_schema(tool_name: str):
    """获取工具的参数Schema"""
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
        schema["args"] = tool_func.args_schema.schema()

    return schema
