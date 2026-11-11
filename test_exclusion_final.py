"""验证方案：自定义 tool exclusion middleware"""
import os
from dotenv import load_dotenv
load_dotenv(override=True)

from typing import Callable
from langchain.agents.middleware.types import AgentMiddleware
from langgraph.prebuilt.tool_node import ToolCallRequest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ResponseT

from deepagents import create_deep_agent, HarnessProfile, register_harness_profile

# 注册 profile（作为双保险）
register_harness_profile(
    'anthropic',
    HarnessProfile(excluded_tools=frozenset({
        'ls', 'read_file', 'write_file', 'edit_file', 'glob', 'grep', 'execute', 'task',
    })),
)

# ====== 方案：自定义 wrap_model_call, 过滤工具 ======
EXCLUDED_BUILTIN_TOOLS = {
    'ls', 'read_file', 'write_file', 'edit_file', 'glob', 'grep',
    'execute', 'task',
}

class ToolFilterMiddleware(AgentMiddleware):
    """过滤掉 DeepAgents 内置的虚拟文件系统工具。

    这些工具在 Windows 实机上不可用：
    - ls/read_file/write_file/edit_file/glob/grep: 基于虚拟文件系统，不支持 Windows 路径
    - execute: 需要 sandbox backend
    - task: 子 agent 工具，暂不需要
    """

    def wrap_model_call(
        self,
        request: ModelRequest[ResponseT],
        handler: Callable,
    ):
        """在模型调用前过滤工具列表。"""
        filtered = [t for t in request.tools if _tool_name(t) not in EXCLUDED_BUILTIN_TOOLS]
        request = request.override(tools=filtered)
        return handler(request)


def _tool_name(tool) -> str | None:
    """提取工具名（兼容 BaseTool 和 dict 格式）。"""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    return getattr(tool, "name", None)


from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from backend.tools.search_files import search_files

model = ChatAnthropic(
    model_name=os.environ.get('ANTHROPIC_MODEL'),
    base_url=os.environ['ANTHROPIC_BASE_URL'],
    api_key=os.environ['ANTHROPIC_AUTH_TOKEN'],
    temperature=0,
)

# 使用自定义 middleware
agent = create_deep_agent(
    model=model,
    tools=[search_files],
    middleware=[ToolFilterMiddleware()],
    checkpointer=MemorySaver(),
)

print('Sending message: list files in C:\\Users...')
result = agent.invoke(
    {'messages': [{'role': 'user', 'content': 'say hello only, no tools'}]},
    config={'configurable': {'thread_id': 'final-test'}},
)
m = result.get('messages', [])[-1]
content = getattr(m, 'content', '') or ''
if isinstance(content, list):
    content = ' '.join(str(c.get('text', '')) for c in content if isinstance(c, dict))
print(f'Response: {content[:200]}')
print('\nSUCCESS: Custom middleware approach works.')
