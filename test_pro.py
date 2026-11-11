"""用 loop.py 的真实 create_agent + 完整 SYSTEM_PROMPT 测试。"""
import os, sys, json
from dotenv import load_dotenv
load_dotenv(override=True)

from langgraph.checkpoint.memory import MemorySaver
from backend.agents.loop import create_agent

# 自动确认
import backend.middleware.HumanConfirmMiddleware as hcm
hcm._ask_user_confirmation = lambda s: True

TEST_DIR = r"C:\Users\86159\Desktop\临时站\bear-agent"

def chat(agent, prompt: str, thread_id: str = "1"):
    print(f"\n{'='*60}")
    print(f">>> {prompt}")
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
            if isinstance(tcs, dict): tcs = [tcs]
            for tc in tcs:
                if isinstance(tc, str):
                    try: tc = json.loads(tc)
                    except: continue
                name = tc.get('name', '?')
                args = tc.get('args', {})
                if isinstance(args, str):
                    try: args = json.loads(args)
                    except: pass
                if isinstance(args, dict):
                    sp = str(args.get('search_path', args.get('path', '')))[:80]
                    tool_names[name] = tool_names.get(name, 0) + 1
                    if tool_names[name] <= 3:  # 只显示前3次的参数
                        print(f"  [{name}] search_path={sp}")
        elif hasattr(msg, 'name') and hasattr(msg, 'content'):
            c = str(getattr(msg, 'content', ''))
            if 'Error' in c or c.startswith('{'):
                print(f"  [result] {msg.name}: {c[:200]}")
        elif hasattr(msg, 'text') and msg.text and msg.text.strip():
            print(f"  [LLM] {msg.text[:300]}")

    print(f"\n  统计: {dict(tool_names)}")
    return tool_names


if __name__ == "__main__":
    agent = create_agent()

    chat(agent, f"看看 {TEST_DIR}\\test_docs 里面有什么文件，占多少空间")
    chat(agent, f"帮我在 {TEST_DIR}\\test_docs 找找有没有重复文件", thread_id="2")
