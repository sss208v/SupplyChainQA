"""
新增通用工具单元测试

测试 web_search、calculator、code_interpreter。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestCalculator:
    """calculator 数学表达式求值"""

    @pytest.mark.asyncio
    async def test_basic_arithmetic(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "2 + 3"})
        assert result == "5"

    @pytest.mark.asyncio
    async def test_multiplication(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "1200 * 0.85 + 500"})
        assert float(result) == pytest.approx(1520.0)

    @pytest.mark.asyncio
    async def test_parentheses(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "(10 + 5) * 2"})
        assert result == "30"

    @pytest.mark.asyncio
    async def test_power(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "2 ** 10"})
        assert result == "1024"

    @pytest.mark.asyncio
    async def test_math_functions(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "sqrt(144)"})
        assert float(result) == pytest.approx(12.0)

    @pytest.mark.asyncio
    async def test_invalid_expression(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "import os"})
        assert "失败" in result or "不支持" in result

    @pytest.mark.asyncio
    async def test_division_by_zero(self):
        from app.core.tool_engine import calculator
        result = await calculator.ainvoke({"expression": "1 / 0"})
        assert "失败" in result


class TestCodeInterpreter:
    """code_interpreter 代码沙箱"""

    @pytest.mark.asyncio
    async def test_simple_print(self):
        from app.core.tool_engine import code_interpreter
        result = await code_interpreter.ainvoke({"code": "print('hello')"})
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_math_module(self):
        from app.core.tool_engine import code_interpreter
        # math 模块已预注入沙箱，无需 import（import 被 AST 安全检查拦截）
        result = await code_interpreter.ainvoke({"code": "print(math.sqrt(16))"})
        assert "4.0" in result

    @pytest.mark.asyncio
    async def test_list_comprehension(self):
        from app.core.tool_engine import code_interpreter
        result = await code_interpreter.ainvoke({"code": "print([x**2 for x in range(5)])"})
        assert "0" in result and "1" in result and "16" in result

    @pytest.mark.asyncio
    async def test_forbidden_import(self):
        from app.core.tool_engine import code_interpreter
        result = await code_interpreter.ainvoke({"code": "import os\nos.system('ls')"})
        assert "安全限制" in result

    @pytest.mark.asyncio
    async def test_forbidden_open(self):
        from app.core.tool_engine import code_interpreter
        result = await code_interpreter.ainvoke({"code": "open('/etc/passwd')"})
        # open() 不在安全 builtins 中，应被拦截（NameError 或安全限制）
        assert "安全限制" in result or "Error" in result or "not defined" in result

    @pytest.mark.asyncio
    async def test_no_output(self):
        from app.core.tool_engine import code_interpreter
        result = await code_interpreter.ainvoke({"code": "x = 1 + 1"})
        assert "成功" in result


class TestWebSearch:
    """web_search 互联网搜索"""

    @pytest.mark.asyncio
    async def test_returns_string(self):
        from app.core.tool_engine import web_search
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "AbstractText": "Test abstract",
            "RelatedTopics": [{"Text": "Topic 1"}, {"Text": "Topic 2"}],
        }
        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await web_search.ainvoke({"query": "test"})
        assert "摘要" in result or "Topic" in result


class TestToolRegistry:
    """工具注册表完整性"""

    def test_all_11_tools_registered(self):
        from app.core.tool_engine import TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == 11

    def test_new_tools_present(self):
        from app.core.tool_engine import TOOL_REGISTRY
        assert "web_search" in TOOL_REGISTRY
        assert "calculator" in TOOL_REGISTRY
        assert "code_interpreter" in TOOL_REGISTRY
