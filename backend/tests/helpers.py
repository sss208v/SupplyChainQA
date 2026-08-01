"""测试通用 helpers — 消除跨测试文件的脚手架重复。

被多个测试文件重复的 mock 构造 / 请求封装统一放在这里；
pytest 不会收集本文件（不以 test_ 开头），测试文件用
`from helpers import ...` 引用。
"""
import io
import json
from unittest.mock import AsyncMock, MagicMock


def ai_message(name: str, args: dict, cid: str = "tc1"):
    """构造带 tool_calls 的 AIMessage。"""
    from langchain_core.messages import AIMessage

    m = AIMessage(content="")
    m.tool_calls = [{"name": name, "args": args, "id": cid}]
    return m


def ai_final(text: str):
    """构造最终回答消息（无 tool_calls）。"""
    from langchain_core.messages import AIMessage

    m = AIMessage(content=text)
    m.tool_calls = []
    return m


def make_mock_tool(name="test_tool"):
    """创建一个 mock LangChain Tool。"""
    t = MagicMock()
    t.name = name
    t.ainvoke = AsyncMock(return_value=f"{name} result")
    return t


def async_iter(items):
    """把可迭代对象包装为 async generator。"""

    async def _gen():
        for item in items:
            yield item

    return _gen()


def parse_sse(raw: str) -> dict:
    """解析 SSE data 行为 dict，跳过 [DONE] 标记。"""
    if "[DONE]" in raw:
        return {"type": "done"}
    data_str = raw.replace("data: ", "").strip()
    return json.loads(data_str)


async def collect_sse(gen) -> list[dict]:
    """收集 async generator 产出的所有 SSE 事件为 dict 列表。"""
    return [parse_sse(item) async for item in gen]


def patch_auth(monkeypatch):
    """把认证依赖替换为匿名假用户（绕过真实登录）。"""
    from app.core import auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_current_user_required", _async_fake_user())


async def post_feedback(client, monkeypatch, payload, token=None):
    """POST /api/v1/feedback（自动 patch 认证依赖），返回 resp。"""
    patch_auth(monkeypatch)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return await client.post("/api/v1/feedback", json=payload, headers=headers)


async def upload_knowledge(client, token, security_group=None,
                           filename="a.txt", content=b"x"):
    """POST /api/v1/knowledge/upload（security_group 可选），返回 resp。"""
    data = {"security_group": security_group} if security_group else None
    return await client.post(
        "/api/v1/knowledge/upload",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )


def fake_langfuse(monkeypatch, span=None, trace_error=None, use_obs=None):
    """构造 fake langfuse 链（span → trace → lf）并 patch obs._get_langfuse。

    返回 (fake_span, fake_trace, fake_lf)；trace_error 非空时 fake_lf.trace 抛异常。
    """
    if use_obs is None:
        from app.core import observability as obs

        use_obs = obs
    fake_span = span if span is not None else MagicMock()
    fake_trace = MagicMock()
    fake_trace.span.return_value = fake_span
    fake_lf = MagicMock()
    if trace_error:
        fake_lf.trace.side_effect = trace_error
    else:
        fake_lf.trace.return_value = fake_trace
    monkeypatch.setattr(use_obs, "_get_langfuse", lambda: fake_lf)
    return fake_span, fake_trace, fake_lf


def capture_record_local(monkeypatch, use_obs=None):
    """patch obs._record_local 捕获调用，返回 captured dict。"""
    if use_obs is None:
        from app.core import observability as obs

        use_obs = obs
    captured = {}
    monkeypatch.setattr(
        use_obs, "_record_local", lambda *a, **kw: captured.update(a=a, kw=kw)
    )
    return captured


def fake_settings(monkeypatch, pk="pk-test", sk="sk-test",
                  host="https://cloud.langfuse.com"):
    """patch config.get_settings 返回带 langfuse 字段的 MagicMock。"""
    from app import config as config_mod

    settings = MagicMock()
    settings.LANGFUSE_PUBLIC_KEY = pk
    settings.LANGFUSE_SECRET_KEY = sk
    settings.LANGFUSE_HOST = host
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    return settings


def _async_fake_user():
    """生成可 await 的 mock auth 依赖"""

    async def fake(request):
        return {"user_id": "u1"}

    return fake
