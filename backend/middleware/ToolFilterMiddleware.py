"""Bear Agent 工具过滤中间件。

过滤 DeepAgents 框架注入的虚拟文件系统工具，这些工具在 Windows 实机环境下不可用：

- ls/read_file/write_file/edit_file/glob/grep: 基于虚拟文件系统（StateBackend），
  不支持 Windows 绝对路径（如 C:\\Users），会报错："Windows absolute paths are not supported"
- execute: 需要 sandbox 后端（SandboxBackendProtocol），无沙箱时返回错误
- task: 子 agent 调度工具，当前暂不需要

过滤发生在 wrap_model_call 阶段，在模型看到工具列表之前，避免 LLM 浪费 token 调用不可用工具。

使用方式：
    from backend.middleware.ToolFilterMiddleware import ToolFilterMiddleware

    agent = create_deep_agent(
        ...,
        middleware=[ToolFilterMiddleware(), ...],
    )

设计决策：
    - 在 wrap_model_call 中过滤，而非 wrap_tool_call：提前处理，模型根本看不到这些工具
    - 配合 register_harness_profile 双保险：即使框架修复了 _ToolExclusionMiddleware，仍然生效
"""
import logging
from typing import Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.tools import BaseTool

logger = logging.getLogger("bear_agent.tool_filter")

# 需要过滤的 DeepAgents 内置工具
_EXCLUDED_TOOLS: frozenset[str] = frozenset({
    "ls", "read_file", "write_file", "edit_file", "glob", "grep",
    "execute", "task",
})


def _tool_name(tool: BaseTool | dict) -> str | None:
    """提取工具名（兼容 BaseTool 和 dict 格式）。"""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class ToolFilterMiddleware(AgentMiddleware):
    """过滤 DeepAgents 内置虚拟文件系统工具。

    在模型调用前从工具列表中移除不可用的内置工具，使 LLM 只能看到
    我们自己的以及 write_todos（无害且有用）。

    钩子：wrap_model_call

    执行位置：应在所有依赖这些工具的中间件（FilesystemMiddleware 等）之后，
    通常放在用户 middleware 列表中靠前的位置即可。
    """

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable,
    ) -> ModelResponse[ResponseT]:
        """在模型调用前过滤工具列表。

        从 request.tools 中移除 _EXCLUDED_TOOLS 中的工具，
        然后传递过滤后的请求给 handler。
        """
        if not request.tools:
            return handler(request)

        filtered = [
            t for t in request.tools
            if _tool_name(t) not in _EXCLUDED_TOOLS
        ]

        removed_count = len(request.tools) - len(filtered)
        if removed_count > 0:
            removed_names = [
                _tool_name(t) for t in request.tools
                if _tool_name(t) in _EXCLUDED_TOOLS
            ]
            logger.debug(
                "已过滤 %d 个内置工具: %s，剩余 %d 个",
                removed_count, removed_names, len(filtered),
            )

        request = request.override(tools=filtered)
        return handler(request)
