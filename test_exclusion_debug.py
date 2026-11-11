"""排查 _ToolExclusionMiddleware 过滤失败的原因"""
import os, sys
from dotenv import load_dotenv
load_dotenv(override=True)

from deepagents import create_deep_agent, HarnessProfile, register_harness_profile
import deepagents.middleware._tool_exclusion as te

original_wrap = te._ToolExclusionMiddleware.wrap_model_call

def debug_wrap(self, request, handler):
    excluded = self._excluded
    print(f'\n[DEBUG] Excluded set: {excluded}')
    print(f'[DEBUG] Excluded type: {type(excluded)}')
    for t in request.tools:
        raw_name = te._tool_name(t)
        print(f'  Tool: {raw_name!r}  type={type(raw_name)}  in_excluded={raw_name in excluded}')
    # 强制执行过滤
    filtered = [t for t in request.tools if te._tool_name(t) not in self._excluded]
    print(f'[DEBUG] Before: {len(request.tools)} tools, After: {len(filtered)} tools')
    request = request.override(tools=filtered)
    return handler(request)

te._ToolExclusionMiddleware.wrap_model_call = debug_wrap

register_harness_profile(
    'anthropic',
    HarnessProfile(excluded_tools=frozenset({
        'ls', 'read_file', 'write_file', 'edit_file', 'glob', 'grep', 'execute', 'task',
    })),
)

from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from backend.tools.search_files import search_files

model = ChatAnthropic(
    model_name=os.environ.get('ANTHROPIC_MODEL'),
    base_url=os.environ['ANTHROPIC_BASE_URL'],
    api_key=os.environ['ANTHROPIC_AUTH_TOKEN'],
    temperature=0,
)

agent = create_deep_agent(
    model=model,
    tools=[search_files],
    checkpointer=MemorySaver(),
)

result = agent.invoke(
    {'messages': [{'role': 'user', 'content': 'say hello, no tools'}]},
    config={'configurable': {'thread_id': 'debug3'}},
)
