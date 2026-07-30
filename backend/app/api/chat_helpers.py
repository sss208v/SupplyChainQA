"""chat.py 辅助函数 + Pydantic 模型 — 从 chat.py 拆出以减小单文件体积"""
import json
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---- 角色中文标签 ----
ROLE_LABELS = {
    "admin": "管理员",
    "purchase": "采购部",
    "warehouse": "仓库部",
    "quality": "质量部",
    "production": "生产部",
    "finance": "财务部",
    "logistics": "物流部",
}


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


# ---- Pydantic 请求模型 ----

# doc_id 合法格式（UUID 字符集），与 milvus_client._DOC_ID_PATTERN 保持一致；
# 在请求模型层提前拦截，SSE 流开始后无法再返回 400
_DOC_ID_RE = re.compile(r"^[0-9a-fA-F-]{1,36}$")


def _validate_doc_ids(doc_ids: Optional[list[str]]) -> Optional[list[str]]:
    if doc_ids:
        for d in doc_ids:
            if not isinstance(d, str) or not _DOC_ID_RE.match(d):
                raise ValueError(f"非法文档ID格式: {d!r}")
    return doc_ids


class ChatRequest(BaseModel):
    """对话请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: Optional[str] = Field(None, description="会话ID（为空则新建）")
    stream: bool = Field(True, description="是否流式输出")
    doc_ids: Optional[list[str]] = Field(None, description="限定检索的文档ID")
    agent_type: Optional[str] = Field(None, description="Agent类型: react（LangGraph ReAct,默认）/ langgraph（多节点循环）/ langchain（AgentExecutor备选），为空则使用配置默认值")
    approved: bool = Field(False, description="是否已确认执行写操作")
    approved_tool: Optional[str] = Field(None, description="已确认执行的工具名")
    images: Optional[list[str]] = Field(None, description="图片列表（base64编码，不含data:前缀）")

    @field_validator("doc_ids")
    @classmethod
    def check_doc_ids(cls, v):
        return _validate_doc_ids(v)


class _AskRequest(BaseModel):
    """RAG 评估专用请求"""
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    doc_ids: Optional[list[str]] = Field(None, description="限定检索的文档ID")

    @field_validator("doc_ids")
    @classmethod
    def check_doc_ids(cls, v):
        return _validate_doc_ids(v)


# ---- 辅助函数 ----

def _sse_format(data: dict) -> str:
    """格式化SSE消息（内部使用）"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---- 公共 SSE 工具函数 ----

def sse_event(event_type: str, **kwargs) -> str:
    """构建统一 SSE 事件字符串"""
    data = {"type": event_type, **kwargs}
    return _sse_format(data)


def sse_done() -> str:
    """返回 SSE 结束标记"""
    return "data: [DONE]\n\n"


def sse_error(message: str, detail: str = None) -> str:
    """构建 SSE 错误事件"""
    data = {"type": "error", "message": message}
    if detail:
        data["detail"] = detail
    return _sse_format(data)


def _handle_greeting(query: str) -> str:
    """处理问候意图"""
    greetings = {
        "你好": "你好！我是供应链智能助手，可以帮你查询制度规范、库存订单、创建工单。",
        "嗨": "嗨！我是供应链智能助手，有什么可以帮你的？",
        "在吗": "在的！随时为你服务，请告诉我你想了解什么？",
        "谢谢": "不客气！如果还有其他问题，随时问我😊",
        "再见": "再见！祝你有美好的一天！",
    }
    for key, response in greetings.items():
        if key in query:
            return response
    return ""


def _build_rag_demo_answer(query: str, results: list, context: str) -> str:
    """Demo mode: build answer from retrieval results without LLM."""
    if not results:
        return f"[演示模式] 知识库中未找到与「{query}」直接相关的内容。接入 LLM API 后可获得智能回答。"
    top_sources = [r.get("source", "未知来源") for r in results[:3]]
    snippets = [r.get("content", "")[:200] for r in results[:2]]
    answer = f"[演示模式] 根据知识库检索，找到 {len(results)} 条相关内容：\n\n"
    for i, (src, snip) in enumerate(zip(top_sources, snippets), 1):
        answer += f"{i}. 来源: {src}\n   摘要: {snip}...\n\n"
    answer += "接入 DeepSeek API 后即可获得基于以上检索结果的智能回答。当前检索耗时已计入性能指标。"
    return answer
