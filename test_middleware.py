from unittest.mock import MagicMock, patch
from langchain_core.messages import ToolMessage
from backend.middleware.ToolErrorMiddleware import ToolErrorMiddleware


def test_tool_error_middleware_catches_exception():
    """测试中间件捕获异常并返回结构化错误。"""
    middleware = ToolErrorMiddleware()

    # 模拟 request
    request = MagicMock()
    request.tool_call = {
        "name": "search_files",
        "args": {"query": "*.pdf"},
        "id": "test-123",
    }

    # 模拟 handler 抛异常
    def handler_that_raises(req):
        raise ValueError("路径不存在")

    result = middleware.wrap_tool_call(request, handler_that_raises)

    # 验证返回的是错误消息
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "search_files" in result.content
    assert "ValueError" in result.content
    assert result.tool_call_id == "test-123"


def test_tool_error_middleware_passes_success():
    """测试正常执行时中间件透传结果。"""
    middleware = ToolErrorMiddleware()

    request = MagicMock()
    request.tool_call = {"name": "search_files", "args": {}, "id": "test-123"}

    expected_result = ToolMessage(content="success", tool_call_id="test-123")

    def handler_that_succeeds(req):
        return expected_result

    result = middleware.wrap_tool_call(request, handler_that_succeeds)

    assert result == expected_result