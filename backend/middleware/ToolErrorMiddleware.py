"""Bear Agent 工具错误处理中间件。

捕获工具调用过程中的异常，返回结构化错误信息，避免 Agent 对话崩溃。
LLM 看到错误信息后可以换种方式尝试，或向用户解释问题。

使用方式：
    from backend.middleware.ToolErrorMiddleware import ToolErrorMiddleware

    agent = create_deep_agent(
        ...,
        middleware=[ToolErrorMiddleware()],
    )

设计决策：
    - 不做复杂重试：避免与 FallbackSearcher 的搜索重试重复
    - 返回结构化错误：包含 error_code、retryable、suggestion，让 LLM 稳定决策
    - 异常分类表：常见异常自动映射到标准错误码和建议
    - 记录完整异常信息：方便调试和问题定位
"""
import logging
import shutil
from typing import Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger("bear_agent.tool_error")

# 参数截断长度，避免错误消息过长
_ARGS_TRUNCATE_LENGTH = 200

# ──────────────────────────────────────────────
#  异常分类表
# ──────────────────────────────────────────────

# 异常类型 → (error_code, retryable, suggestion)
_EXCEPTION_CLASSIFICATION: dict[type[Exception], tuple[str, bool, str]] = {
    PermissionError: (
        "PERMISSION_DENIED", False,
        "请检查文件权限或选择其他路径",
    ),
    FileNotFoundError: (
        "FILE_NOT_FOUND", False,
        "请确认路径是否正确，文件是否已被移动或删除",
    ),
    FileExistsError: (
        "FILE_EXISTS", False,
        "目标文件已存在，可尝试设置 on_conflict='overwrite' 覆盖或 on_conflict='skip' 跳过",
    ),
    IsADirectoryError: (
        "IS_A_DIRECTORY", False,
        "期望的是文件但提供的路径是一个目录",
    ),
    NotADirectoryError: (
        "NOT_A_DIRECTORY", False,
        "期望的是目录但提供的路径是一个文件",
    ),
    TimeoutError: (
        "TIMEOUT", True,
        "操作超时，可以缩小搜索范围或增加超时时间后重试",
    ),
    shutil.SameFileError: (
        "SAME_FILE", False,
        "源文件和目标文件是同一个文件",
    ),
    NotImplementedError: (
        "NOT_IMPLEMENTED", False,
        "该功能尚未实现",
    ),
    ValueError: (
        "INVALID_PARAMETER", False,
        "请检查参数值是否符合要求",
    ),
    TypeError: (
        "TYPE_ERROR", False,
        "参数类型不正确，请检查参数格式",
    ),
}

# 默认兜底分类
_DEFAULT_CLASSIFICATION: tuple[str, bool, str] = (
    "INTERNAL_ERROR", False,
    "内部错误，请稍后重试或缩小操作范围",
)

# OSError errno 特定处理
_ERRNO_CLASSIFICATION: dict[int, tuple[str, str]] = {
    28: ("DISK_FULL", "磁盘空间不足，请清理磁盘后重试"),
    13: ("PERMISSION_DENIED", "访问被拒绝，请检查文件权限"),
    2:  ("FILE_NOT_FOUND", "文件或目录不存在"),
    17: ("FILE_EXISTS", "文件已存在"),
    39: ("DIRECTORY_NOT_EMPTY", "目录不为空，请先清空目录"),
}


# ──────────────────────────────────────────────
#  异常分类
# ──────────────────────────────────────────────

def _classify_exception(e: Exception) -> tuple[str, bool, str]:
    """分类异常，返回 (error_code, retryable, suggestion)。

    优先级：OSError errno → 类型精确匹配 → 默认兜底
    """
    # OSError 子类优先检查 errno（更精确）
    if isinstance(e, OSError) and hasattr(e, 'errno') and e.errno:
        errno_code = e.errno
        if errno_code in _ERRNO_CLASSIFICATION:
            code, suggestion = _ERRNO_CLASSIFICATION[errno_code]
            retryable = (errno_code == 28)  # 磁盘满可能清完就恢复
            return code, retryable, suggestion

    # 类型精确匹配
    for exc_type, (code, retryable, suggestion) in _EXCEPTION_CLASSIFICATION.items():
        if isinstance(e, exc_type):
            return code, retryable, suggestion

    # 兜底
    return _DEFAULT_CLASSIFICATION


# ──────────────────────────────────────────────
#  错误格式化
# ──────────────────────────────────────────────

def _format_structured_error(
    tool_name: str,
    error_type: str,
    error_message: str,
    error_code: str,
    retryable: bool,
    suggestion: str,
) -> str:
    """格式化结构化错误消息，方便 LLM 稳定解析各字段。"""
    return (
        f"[ERROR] {tool_name} 执行失败\n"
        f"  错误类型: {error_type}\n"
        f"  错误代码: {error_code}\n"
        f"  错误信息: {error_message}\n"
        f"  可重试: {'是' if retryable else '否'}\n"
        f"  建议: {suggestion}"
    )


class ToolErrorMiddleware(AgentMiddleware):
    """工具错误处理中间件，捕获工具异常并返回结构化错误。

    功能：
    - 捕获工具和后续中间件的异常
    - 返回结构化错误信息（ToolMessage with status="error"）
    - 记录完整异常堆栈，方便调试
    - 避免 Agent 对话因为工具异常而崩溃

    钩子：wrap_tool_call

    执行顺序：
        ObservabilityMiddleware → ToolErrorMiddleware → 工具执行
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """包裹工具调用，捕获异常并返回结构化错误。

        Args:
            request: 工具调用请求，包含 tool_call（name, args, id）等。
            handler: 执行工具的函数，必须调用。

        Returns:
            工具执行结果（ToolMessage 或 Command），或错误信息。
        """
        tool_name = request.tool_call.get("name", "unknown")
        tool_args = request.tool_call.get("args", {})
        tool_call_id = request.tool_call.get("id", "")

        # 截断参数避免错误消息过长
        args_str = str(tool_args)
        if len(args_str) > _ARGS_TRUNCATE_LENGTH:
            args_str = args_str[:_ARGS_TRUNCATE_LENGTH] + "..."

        try:
            return handler(request)
        except Exception as e:
            # 记录完整异常信息（包含堆栈）
            logger.error(
                "工具调用异常 | %s(%s) | 错误: %s",
                tool_name, args_str, str(e),
                exc_info=True,
            )

            # 分类异常，获取结构化字段
            error_code, retryable, suggestion = _classify_exception(e)

            # 构造结构化错误消息
            error_message = _format_structured_error(
                tool_name=tool_name,
                error_type=type(e).__name__,
                error_message=str(e),
                error_code=error_code,
                retryable=retryable,
                suggestion=suggestion,
            )

            return ToolMessage(
                content=error_message,
                tool_call_id=tool_call_id,
                status="error",
            )
