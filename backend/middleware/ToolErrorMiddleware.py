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
    - 返回结构化错误：让 LLM 保持控制权，可以换种方式尝试
    - 记录完整异常信息：方便调试和问题定位
"""
import logging
from typing import Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger("bear_agent.tool_error")

# 错误消息模板
_ERROR_MESSAGE_TEMPLATE = "工具 {tool_name} 执行失败: {error_type}: {error_message}"

# 参数截断长度，避免错误消息过长
_ARGS_TRUNCATE_LENGTH = 200


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

            # 构造结构化错误消息
            error_message = _ERROR_MESSAGE_TEMPLATE.format(
                tool_name=tool_name,
                error_type=type(e).__name__,
                error_message=str(e),
            )

            return ToolMessage(
                content=error_message,
                tool_call_id=tool_call_id,
                status="error",
            )
