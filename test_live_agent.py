"""直接给 Agent 发自然语言指令，观察完整工具调用链路。"""
import os, sys, json, time
from dotenv import load_dotenv
load_dotenv(override=True)

from langgraph.checkpoint.memory import MemorySaver
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent

from backend.tools.search_files import search_files
from backend.tools.find_duplicates import find_duplicates
from backend.tools.file_stats import file_stats
from backend.tools.file_metadata import file_metadata
from backend.tools.copy_files import copy_files
from backend.tools.move_files import move_files
from backend.tools.delete_files import delete_files
from backend.tools.rename_file import rename_file
from backend.middleware.ObservabilityMiddleware import ObservabilityMiddleware
from backend.middleware.ToolErrorMiddleware import ToolErrorMiddleware
from backend.middleware.ToolPolicyMiddleware import ToolPolicyMiddleware
from backend.middleware.HumanConfirmMiddleware import HumanConfirmMiddleware

TEST_DIR = r"C:\Users\86159\Desktop\临时站\bear-agent"

# ====== Monkey-patch: 自动确认所有操作，避免 input() 阻塞 ======
import backend.middleware.HumanConfirmMiddleware as hcm
_original_ask = hcm._ask_user_confirmation
def _auto_confirm(summary):
    print(f"\n  [CONFIRM] 自动确认操作:\n{summary}")
    return True
hcm._ask_user_confirmation = _auto_confirm

SYSTEM_PROMPT = f"""你是 Bear Agent，Windows 文件管理助手。

测试目录：{TEST_DIR}
子目录：test_docs（文档）、test_photos（照片）、junk（垃圾文件）

可用工具及参数：
- search_files(query, search_path, extension, min_size_mb, max_size_mb, modified_after, modified_before, max_results, result_id, page)
- file_stats(path, largest_n)
- find_duplicates(search_path, extension, result_id, page)。返回句柄含 result_id，可直接传给 copy_files/move_files/delete_files
- copy_files(target_dir, result_id, sources, on_conflict)。on_conflict: skip/overwrite/error，默认 skip
- move_files(target_dir, result_id, sources, on_conflict)。on_conflict: skip/overwrite/error，默认 skip
- delete_files(result_id, sources, to_recycle_bin)。默认回收站
- rename_file(path, new_name)

原则：能用专用工具就用专用工具，按顺序组合。不要虚构不存在的参数。"""

def make_agent():
    model = ChatAnthropic(
        model_name=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash"),
        base_url=os.environ["ANTHROPIC_BASE_URL"],
        api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
        temperature=0,
    )
    return create_deep_agent(
        model=model,
        tools=[search_files, find_duplicates, file_stats, file_metadata,
               copy_files, move_files, delete_files, rename_file],
        system_prompt=SYSTEM_PROMPT,
        middleware=[ObservabilityMiddleware(), ToolErrorMiddleware(),
                    ToolPolicyMiddleware(), HumanConfirmMiddleware()],
        checkpointer=MemorySaver(),
    )


def _maybe_dict(obj):
    """安全地获取字典，处理各种类型。"""
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            return {"raw": obj[:100]}
    if hasattr(obj, '__dict__'):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
    return {"raw": str(obj)[:100]}


def chat(agent, prompt: str, thread_id: str = "1"):
    """发送一句话，实时打印完整消息流。"""
    print(f"\n{'='*60}")
    print(f"用户: {prompt}")
    print(f"{'='*60}")

    config = {"configurable": {"thread_id": thread_id}}
    stream = agent.stream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        config=config,
        version="v3",
    )

    tool_calls = 0
    for msg in stream.messages:
        # 工具调用请求 (AIMessage with tool_calls)
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            tcs = msg.tool_calls
            if isinstance(tcs, str):
                tcs = json.loads(tcs)
            if isinstance(tcs, dict):
                tcs = [tcs]
            for tc in tcs:
                if isinstance(tc, str):
                    tc = json.loads(tc)
                if isinstance(tc, dict):
                    tool_calls += 1
                    name = tc.get('name', '?')
                    args = tc.get('args', {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    if isinstance(args, dict):
                        short = {k: (str(v)[:120]+'...' if len(str(v))>120 else str(v)) for k,v in args.items()}
                        print(f"\n  [TOOL #{tool_calls}] {name}({json.dumps(short, ensure_ascii=False)})")
                    else:
                        print(f"\n  [TOOL #{tool_calls}] {name}({str(args)[:200]})")

        # 工具返回结果 (ToolMessage)
        elif hasattr(msg, 'name') and hasattr(msg, 'content'):
            name = getattr(msg, 'name', '?')
            content = str(getattr(msg, 'content', ''))
            status = getattr(msg, 'status', '')
            status_str = f" status={status}" if status else ""
            if len(content) > 300:
                content = content[:300] + f"...(共{len(str(msg.content))}字)"
            print(f"  [RESULT] {name}: {content}{status_str}")

        # LLM 文本回复
        elif hasattr(msg, 'text') and msg.text:
            print(f"  [LLM] {msg.text}")

    print(f"\n--- {tool_calls} 次工具调用 ---")
    return tool_calls


if __name__ == "__main__":
    agent = make_agent()

    # 场景 1: 查看目录
    chat(agent, f"帮我看看 {TEST_DIR}\\test_docs 里面有什么文件，占多少空间")

    # 场景 2: 找重复文件
    chat(agent, f"帮我在 {TEST_DIR}\\test_docs 找找重复文件", thread_id="2")

    # 场景 3: find_duplicates → copy_files 备份
    chat(agent, f"把刚才找到的重复文件全部复制到 {TEST_DIR}\\backup 备份一下", thread_id="3")

    # 场景 4: 讨论问用户（不调用工具，纯对话）
    chat(agent, f"重复文件一般怎么处理比较好？", thread_id="4")
