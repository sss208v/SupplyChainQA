"""
SupplyChainRAG - Chat Intent Handlers

将 chat.py 中 event_generator() 的 7 个意图分支独立为可测试的 handler 函数。
每个 handler 接收所需参数，返回 async generator 产出 SSE 事件字符串。

设计原则：
- 纯函数式：不持有状态，所有依赖通过参数注入
- 可测试：每个 handler 可以独立单元测试（mock 外部依赖）
- 职责单一：一个 handler 只处理一种意图
"""
from .greeting import handle_greeting
from .unclear import handle_unclear
from .rag_answer import handle_rag_answer
from .tool_call import handle_tool_call
from .goal import handle_goal
from .hybrid import handle_hybrid
from .graph_query import handle_graph_query
from .ask import handle_ask

__all__ = [
    "handle_greeting",
    "handle_unclear",
    "handle_rag_answer",
    "handle_tool_call",
    "handle_goal",
    "handle_hybrid",
    "handle_graph_query",
    "handle_ask",
]
