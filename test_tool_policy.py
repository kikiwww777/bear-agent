"""ToolPolicyMiddleware 测试。

测试覆盖：
- normalize_path: 路径归一化（反斜杠、路径穿越）
- sanitize_query: 查询净化（控制字符、空值）
- ToolPolicyMiddleware: 完整中间件流程（归一化 + 净化 + 权限检查）
"""
from unittest.mock import MagicMock

from langchain_core.messages import ToolMessage

from backend.middleware.ToolPolicyMiddleware import (
    normalize_path,
    sanitize_query,
    ToolPolicyMiddleware,
)


# ──────────────────────────────────────────────
#  normalize_path 测试
# ──────────────────────────────────────────────

class TestNormalizePath:
    """路径归一化测试。"""

    def test_double_backslash(self):
        """双反斜杠 → 单反斜杠。"""
        result, error = normalize_path("C:\\\\Users")
        assert result == r"C:\Users"
        assert error is None

    def test_forward_slash(self):
        """正斜杠 → 反斜杠。"""
        result, error = normalize_path("C:/Users")
        assert result == r"C:\Users"
        assert error is None

    def test_quadruple_backslash(self):
        """四反斜杠 → 单反斜杠。"""
        result, error = normalize_path("C:\\\\\\\\Users")
        assert result == r"C:\Users"
        assert error is None

    def test_path_traversal(self):
        """路径穿越被拒绝。"""
        result, error = normalize_path("C:\\..\\Windows")
        assert result is None
        assert ".." in error

    def test_normal_path(self):
        """正常路径不变。"""
        result, error = normalize_path(r"D:\Developer")
        assert result == r"D:\Developer"
        assert error is None

    def test_mixed_slashes(self):
        """混合斜杠统一为反斜杠。"""
        result, error = normalize_path("C:/Users\\Documents/test")
        assert result == r"C:\Users\Documents\test"
        assert error is None


# ──────────────────────────────────────────────
#  sanitize_query 测试
# ──────────────────────────────────────────────

class TestSanitizeQuery:
    """查询净化测试。"""

    def test_normal_query(self):
        """正常查询不变。"""
        assert sanitize_query("*.pdf") == "*.pdf"

    def test_trim_whitespace(self):
        """去首尾空白。"""
        assert sanitize_query("  *.pdf  ") == "*.pdf"

    def test_empty_string(self):
        """空字符串 → 通配符。"""
        assert sanitize_query("") == "*"

    def test_null_byte(self):
        """去掉 null 字节。"""
        assert sanitize_query("*.pdf\x00") == "*.pdf"

    def test_control_characters(self):
        """去掉控制字符。"""
        assert sanitize_query("*.pdf\x01\x02\x03") == "*.pdf"

    def test_only_whitespace(self):
        """纯空白字符串 → 通配符。"""
        assert sanitize_query("   ") == "*"


# ──────────────────────────────────────────────
#  ToolPolicyMiddleware 测试
# ──────────────────────────────────────────────

def _make_request(tool_name: str, args: dict):
    """构造测试用的 request（MagicMock）。"""
    request = MagicMock()
    request.tool_call = {
        "name": tool_name,
        "args": args,
        "id": "test-call-id",
        "type": "tool_call",
    }
    return request


class TestToolPolicyMiddleware:
    """中间件完整流程测试。"""

    def setup_method(self):
        """每个测试前创建中间件实例。"""
        self.middleware = ToolPolicyMiddleware()

    def test_path_normalization(self):
        """路径归一化生效：args 里的双反斜杠变成单反斜杠。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("file_stats", {"path": "C:\\\\Users", "top_n": 10})

        self.middleware.wrap_tool_call(request, handler)

        handler.assert_called_once()
        clean_request = handler.call_args[0][0]
        assert clean_request.tool_call["args"]["path"] == r"C:\Users"
        assert clean_request.tool_call["args"]["top_n"] == 10

    def test_system_dir_read_only_safe_tool_passes(self):
        """系统目录 + SAFE 工具 = 放行（只读操作允许）。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("file_stats", {"path": r"C:\Windows"})

        self.middleware.wrap_tool_call(request, handler)

        handler.assert_called_once()  # file_stats 是 SAFE，放行

    def test_user_dir_safe_tool_passes(self):
        """用户目录 + SAFE 工具 = 放行。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("file_stats", {"path": r"C:\Users"})

        self.middleware.wrap_tool_call(request, handler)

        handler.assert_called_once()

    def test_query_sanitization(self):
        """查询参数被净化。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("search_files", {
            "query": "*.pdf\x00",
            "search_path": r"C:\Users",
        })

        self.middleware.wrap_tool_call(request, handler)

        handler.assert_called_once()

    def test_unknown_tool_passes_through(self):
        """未知工具直接放行（不拦截，只记录日志）。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("some_unknown_tool", {"arg": "value"})

        self.middleware.wrap_tool_call(request, handler)

        handler.assert_called_once()

    def test_path_traversal_blocked(self):
        """路径穿越被拦截。"""
        handler = MagicMock()
        request = _make_request("file_stats", {"path": r"C:\..\Windows"})

        result = self.middleware.wrap_tool_call(request, handler)

        handler.assert_not_called()
        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        assert ".." in result.content

    def test_path_list_normalization(self):
        """路径列表参数被归一化（file_metadata paths）。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("file_metadata", {
            "paths": ["C:\\\\a.jpg", "C:/b.jpg"],
            "fields": ["name"],
        })

        self.middleware.wrap_tool_call(request, handler)

        clean_request = handler.call_args[0][0]
        assert clean_request.tool_call["args"]["paths"] == [r"C:\a.jpg", r"C:\b.jpg"]
        assert clean_request.tool_call["args"]["fields"] == ["name"]

    def test_no_args_does_not_crash(self):
        """空参数不崩溃。"""
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-call-id"))
        request = _make_request("file_stats", {})

        self.middleware.wrap_tool_call(request, handler)

        handler.assert_called_once()

    def test_blocked_path_always_blocked(self):
        """BLOCKED 路径被完全拦截（即使是 SAFE 工具）。"""
        from backend.utils.path_permissions import save_permission
        import os

        # 临时设置一个 BLOCKED 路径
        save_permission(r"D:\禁止访问", "BLOCKED")

        try:
            handler = MagicMock()
            request = _make_request("file_stats", {"path": r"D:\禁止访问"})

            result = self.middleware.wrap_tool_call(request, handler)

            handler.assert_not_called()
            assert isinstance(result, ToolMessage)
            assert result.status == "error"
            assert "禁止访问" in result.content
        finally:
            # 清理
            os.remove("path_permissions.json")
