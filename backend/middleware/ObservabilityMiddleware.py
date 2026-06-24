"""Bear Agent 可观察性中间件。

记录 Agent 运行过程中的关键事件：LLM 调用、工具调用、耗时统计。
用于调试、性能分析和操作审计。

使用方式：
    from backend.agents.middleware import ObservabilityMiddleware

    agent = create_deep_agent(
        ...,
        middleware=[ObservabilityMiddleware()],
    )
"""
import logging
import time
from typing import Any, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger("bear_agent.observability")

# 参数截断长度，避免日志过长
_ARGS_TRUNCATE_LENGTH = 200


class ObservabilityMiddleware(AgentMiddleware):
    """可观察性中间件，记录 Agent 运行过程中的关键事件。

    功能：
    - 记录 Agent 启动/结束
    - 记录 LLM 调用耗时
    - 记录工具调用详情（工具名、参数、耗时、结果状态）

    钩子执行顺序：
        before_agent → (before_model → wrap_model_call → after_model → wrap_tool_call)* → after_agent
    """

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Agent 循环开始前调用。

        Args:
            state: Agent 状态，包含 messages 等字段。
            runtime: 运行时上下文。

        Returns:
            None（不更新状态）。
        """
        logger.info("=" * 50)
        logger.info("Agent 启动")
        logger.info("=" * 50)
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Agent 循环结束后调用。

        Args:
            state: Agent 状态。
            runtime: 运行时上下文。

        Returns:
            None（不更新状态）。
        """
        logger.info("=" * 50)
        logger.info("Agent 结束")
        logger.info("=" * 50)
        return None

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """LLM 调用前调用。

        Args:
            state: Agent 状态，包含 messages 列表。
            runtime: 运行时上下文。

        Returns:
            None（不更新状态）。
        """
        msg_count = len(state.get("messages", []))
        logger.info("LLM 调用前 | 消息数: %d", msg_count)
        return None

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """LLM 调用后调用。

        Args:
            state: Agent 状态。
            runtime: 运行时上下文。

        Returns:
            None（不更新状态）。
        """
        msg_count = len(state.get("messages", []))
        logger.info("LLM 调用后 | 消息数: %d", msg_count)
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """包裹 LLM 调用，记录耗时。

        Args:
            request: LLM 调用请求，包含 model、messages、system_message 等。
            handler: 调用下一个中间件/LLM 的函数，必须调用。

        Returns:
            LLM 响应。
        """
        logger.info("LLM 调用开始")
        t0 = time.time()

        try:
            response = handler(request)
            elapsed = time.time() - t0
            logger.info("LLM 调用完成 | 耗时: %.2fs", elapsed)
            return response
        except Exception as e:
            elapsed = time.time() - t0
            logger.error("LLM 调用失败 | 耗时: %.2fs | 错误: %s", elapsed, e)
            raise

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """包裹工具调用，记录工具名、参数、耗时、结果状态。

        Args:
            request: 工具调用请求，包含 tool_call（name, args, id）等。
            handler: 调用下一个中间件/工具的函数，必须调用。

        Returns:
            工具执行结果（ToolMessage 或 Command）。
        """
        tool_name = request.tool_call.get("name", "unknown")
        tool_args = request.tool_call.get("args", {})

        # 截断参数避免日志过长
        args_str = str(tool_args)
        if len(args_str) > _ARGS_TRUNCATE_LENGTH:
            args_str = args_str[:_ARGS_TRUNCATE_LENGTH] + "..."

        logger.info("工具调用开始 | %s(%s)", tool_name, args_str)
        t0 = time.time()

        try:
            result = handler(request)
            elapsed = time.time() - t0

            # 根据结果类型记录日志
            if isinstance(result, ToolMessage):
                if result.status == "error":
                    logger.warning(
                        "工具调用失败 | %s | 耗时: %.2fs | 错误: %s",
                        tool_name, elapsed, result.content[:100],
                    )
                else:
                    logger.info("工具调用成功 | %s | 耗时: %.2fs", tool_name, elapsed)
            else:
                logger.info("工具调用完成 | %s | 耗时: %.2fs (Command)", tool_name, elapsed)

            return result
        except Exception as e:
            elapsed = time.time() - t0
            logger.error("工具调用异常 | %s | 耗时: %.2fs | 错误: %s", tool_name, elapsed, e)
            raise

