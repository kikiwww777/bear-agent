"""验证 ToolFilterMiddleware + 真实 LLM — 看 ls 是否还在被调用。"""
import os, sys, json
from dotenv import load_dotenv
load_dotenv(override=True)

from langgraph.checkpoint.memory import MemorySaver
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent, HarnessProfile, register_harness_profile

from backend.tools.search_files import search_files
from backend.tools.file_stats import file_stats
from backend.tools.find_duplicates import find_duplicates
from backend.tools.copy_files import copy_files
from backend.tools.move_files import move_files
from backend.tools.delete_files import delete_files

from backend.middleware.ObservabilityMiddleware import ObservabilityMiddleware
from backend.middleware.ToolErrorMiddleware import ToolErrorMiddleware
from backend.middleware.ToolPolicyMiddleware import ToolPolicyMiddleware
from backend.middleware.HumanConfirmMiddleware import HumanConfirmMiddleware
from backend.middleware.ToolFilterMiddleware import ToolFilterMiddleware

# 双保险
register_harness_profile(
    'anthropic',
    HarnessProfile(excluded_tools=frozenset({
        'ls', 'read_file', 'write_file', 'edit_file', 'glob', 'grep', 'execute', 'task',
    })),
)

# 自动确认
import backend.middleware.HumanConfirmMiddleware as hcm
hcm._ask_user_confirmation = lambda s: True

TEST_DIR = r"C:\Users\86159\Desktop\临时站\bear-agent"

def make_agent():
    model = ChatAnthropic(
        model_name=os.environ.get('ANTHROPIC_MODEL', 'deepseek-v4-flash'),
        base_url=os.environ['ANTHROPIC_BASE_URL'],
        api_key=os.environ['ANTHROPIC_AUTH_TOKEN'],
        temperature=0,
    )
    return create_deep_agent(
        model=model,
        tools=[search_files, file_stats, find_duplicates, copy_files, move_files, delete_files],
        system_prompt=f"你是 Bear Agent。测试目录: {TEST_DIR}\\test_docs。优先用专用工具。",
        middleware=[ToolFilterMiddleware(), ObservabilityMiddleware(), ToolErrorMiddleware(),
                    ToolPolicyMiddleware(), HumanConfirmMiddleware()],
        checkpointer=MemorySaver(),
    )


def chat(agent, prompt: str, thread_id: str = "1"):
    """发送指令，打印工具调用。"""
    print(f"\n{'='*60}")
    print(f"> {prompt}")
    print(f"{'='*60}")

    config = {"configurable": {"thread_id": thread_id}}
    stream = agent.stream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        config=config, version="v3",
    )

    tool_names = {}
    for msg in stream.messages:
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            tcs = msg.tool_calls
            if isinstance(tcs, str):
                try: tcs = json.loads(tcs)
                except: tcs = [{}]
            if isinstance(tcs, dict):
                tcs = [tcs]
            for tc in tcs:
                if isinstance(tc, str):
                    try: tc = json.loads(tc)
                    except: continue
                name = tc.get('name', '?')
                tool_names[name] = tool_names.get(name, 0) + 1
        elif hasattr(msg, 'text') and msg.text:
            text = msg.text[:300]
            if text.strip():
                print(f"  [LLM] {text}")

    print(f"\n  工具调用统计: {dict(tool_names)}")
    return tool_names


if __name__ == "__main__":
    agent = make_agent()

    # 场景 1: 查看目录 — 之前会大量调用 ls
    stats1 = chat(agent, f"看看 {TEST_DIR}\\test_docs 里有什么文件")

    # 场景 2: 找重复 — 之前有 28 次 find_duplicates 探索
    stats2 = chat(agent, f"找一下 {TEST_DIR}\\test_docs 中的重复文件", thread_id="2")

    # 判决
    ls_count = stats1.get('ls', 0) + stats2.get('ls', 0)
    if ls_count == 0:
        print("\n>>> SUCCESS: ls 工具已被完全排除!")
    else:
        print(f"\n>>> WARNING: ls 仍然被调用了 {ls_count} 次!")
