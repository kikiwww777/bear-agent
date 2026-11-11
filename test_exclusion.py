"""测试 HarnessProfile excluded_tools 是否真正生效。"""
import os, sys
from dotenv import load_dotenv
load_dotenv(override=True)

# Patch BEFORE creating agent
from deepagents import create_deep_agent, HarnessProfile, register_harness_profile
import deepagents.middleware._tool_exclusion as te
original_wrap = te._ToolExclusionMiddleware.wrap_model_call

def debug_wrap(self, request, handler):
    tool_names = [te._tool_name(t) for t in request.tools]
    has_ls = 'ls' in tool_names
    has_glob = 'glob' in tool_names
    has_grep = 'grep' in tool_names
    has_read = 'read_file' in tool_names
    has_write = 'write_file' in tool_names
    has_edit = 'edit_file' in tool_names
    has_exec = 'execute' in tool_names
    has_task = 'task' in tool_names
    has_todos = 'write_todos' in tool_names
    print(f'\n[DEBUG] Tools in model request ({len(tool_names)}):')
    print(f'  ls={has_ls} glob={has_glob} grep={has_grep}')
    print(f'  read_file={has_read} write_file={has_write} edit_file={has_edit}')
    print(f'  execute={has_exec} task={has_task} write_todos={has_todos}')
    excluded_present = has_ls or has_glob or has_grep or has_read or has_write or has_edit or has_exec or has_task
    print(f'  EXCLUDED_TOOLS_PRESENT: {excluded_present}')
    return original_wrap(self, request, handler)

te._ToolExclusionMiddleware.wrap_model_call = debug_wrap

# 注册 profile
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

print('Sending message to trigger model call...')
result = agent.invoke(
    {'messages': [{'role': 'user', 'content': 'list the files in C:\\Users'}]},
    config={'configurable': {'thread_id': 'debug2'}},
)
m = result.get('messages', [])[-1]
content = getattr(m, 'content', '') or ''
if isinstance(content, list):
    content = ' '.join(str(c.get('text', '')) for c in content if isinstance(c, dict))
print(f'Response: {content[:300]}')
