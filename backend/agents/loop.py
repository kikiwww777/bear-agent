import logging
import os
from dotenv import load_dotenv

# 配置日志：显示时间、模块名、级别、消息
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

load_dotenv(override=True)
from deepagents import create_deep_agent, HarnessProfile, register_harness_profile

# 排除 DeepAgents 内置的虚拟文件系统工具（它们在 Windows 实机上报错）
# ls/read_file/write_file/edit_file/glob/grep: 虚拟文件系统，不支持 Windows 绝对路径
# execute: 需要 sandbox backend，无沙箱时报错
# task: 子 agent，当前暂不需要
register_harness_profile(
    "anthropic",
    HarnessProfile(excluded_tools=frozenset({
        "ls", "read_file", "write_file", "edit_file", "glob", "grep",
        "execute", "task",
    })),
)

from langchain_anthropic import ChatAnthropic
from backend.tools.search_files import search_files
from backend.tools.find_duplicates import find_duplicates
from backend.tools.file_stats import file_stats
from backend.tools.file_metadata import file_metadata
from backend.tools.set_path_permission import set_path_permission
from backend.tools.copy_files import copy_files
from backend.tools.move_files import move_files
from backend.tools.delete_files import delete_files
from backend.tools.rename_file import rename_file
from backend.middleware.ObservabilityMiddleware import ObservabilityMiddleware
from backend.middleware.ToolErrorMiddleware import ToolErrorMiddleware
from backend.middleware.ToolPolicyMiddleware import ToolPolicyMiddleware
from backend.middleware.HumanConfirmMiddleware import HumanConfirmMiddleware
from backend.middleware.ToolFilterMiddleware import ToolFilterMiddleware
from langgraph.checkpoint.memory import MemorySaver


SYSTEM_PROMPT = """你是 Bear Agent，Windows 文件管理助手。帮助用户查找、清理、管理、整理文件。

## 工具使用指南

### 1. 文件搜索（search_files）
搜索文件，支持关键词、路径、扩展名、大小、时间等条件。所有参数直接传值，不需要包装对象。
- 搜索 PDF：search_files(query="*.pdf", search_path="C:\\Users\\Documents")
- 搜索大文件：search_files(query="*", min_size_mb=100)
- 搜索最近文件：search_files(query="*", modified_after="2024-01-01")
- 按扩展名搜索：search_files(query="*", extension=[".jpg", ".png"])
- 获取更多结果：search_files(result_id="xxx", page=2)

### 2. 目录统计（file_stats）
了解目录空间占用，返回分类统计、扩展名统计、最大文件等。
- 查看 C 盘占用：file_stats(path="C:\\")
- 查看最大文件：file_stats(path="C:\\Users", largest_n=10)

### 3. 重复文件（find_duplicates）
查找重复文件，所有参数直接传值，不需要包装对象。
- 新搜索：find_duplicates(search_path="C:\\Users") → 返回句柄 + 前 20 组
- 按类型查：find_duplicates(search_path="D:\\", extension=".pdf")
- 看更多：find_duplicates(result_id="xxx", page=2)
结果按浪费空间降序排列，每组包含完整文件路径，可直接用于 copy_files/delete_files/move_files。
清理重复文件工作流：find_duplicates → 分析结果 → delete_files(result_id="...") 或 copy_files(result_id="...") 备份后再清理。

### 4. 文件元数据（file_metadata）
读取文件的详细信息，包括 EXIF、PDF 属性、音视频信息等。两种模式：
- 单文件：file_metadata(path="C:\\photo.jpg") → 返回完整元数据
- 批量查询：file_metadata(paths=[...], fields=["name", "datetime_original"]) → 返回指定字段

## 清理场景工作流

用户说"帮我清理 XX"或"XX 太大了"时，按以下步骤组合工具：

1. **先看整体**：file_stats(path=..., largest_n=10)
   → 了解哪类文件最多、哪些文件最大
2. **再看重复**：find_duplicates(search_path=...)
   → 了解有多少重复文件、浪费了多少空间
3. **主动问用户**：根据上面的数据，问用户想优先处理什么
   → 不要自己假设用户想删什么
4. **深入调查**：根据用户回答，用 search_files 或 file_metadata 进一步了解
5. **给出建议**：用自然语言组织建议，说明每个建议的理由

关键原则：
- 你是推理者，工具是数据源。你来判断优先级，不要把判断交给工具。
- 主动问用户，不要假设。用户说"清理"可能是腾空间，也可能是整理文件。
- 关联分析：看到大文件时问用户是否还需要，看到重复文件时解释可能的原因。

### 5. 批量复制/移动文件（copy_files / move_files）
将文件复制或移动到目标目录，两种调用方式：
- 通过 result_id：search_files(query="*.pdf") → copy_files(result_id="...", target_dir="E:\\Backup")
- 通过 sources：copy_files(sources=["D:\\a.pdf", "D:\\b.pdf"], target_dir="...")
- 冲突处理 on_conflict：'skip'（默认，跳过已有文件）、'overwrite'（覆盖）、'error'（报错）

注意：sources 是字符串数组，每个元素是一个完整路径。不要把 JSON 数组当字符串传。
正确：sources=["C:\\Users\\file.pdf"]
错误：sources="[\"C:\\Users\\file.pdf\"]"

移动后源文件消失，复制后源文件保留。

### 6. 批量删除文件（delete_files）
删除文件，默认移到回收站（可恢复），设置 to_recycle_bin=False 可永久删除。两种调用方式：
- 通过 result_id（推荐）：search_files(query="*.tmp") → delete_files(result_id="...")
- 通过 sources：delete_files(sources=["D:\\a.tmp", "D:\\b.tmp"])
- 永久删除：delete_files(sources=["D:\\a.tmp"], to_recycle_bin=False)

sources 格式同上，是字符串数组，不是 JSON 字符串。
删除操作不可逆（永久删除时），建议先用 search_files 确认要删除的文件列表。

### 7. 文件重命名（rename_file）
重命名单个文件或目录。new_name 只传文件名，不含路径。
- 重命名文件：rename_file(path="D:\\docs\\old.pdf", new_name="new.pdf")
- 重命名目录：rename_file(path="D:\\old_folder", new_name="new_folder")
目标已存在时会报错，不会覆盖。注意：move_files 不能改名，只能移动位置。

### 8. 路径权限管理（set_path_permission）
用户可以设置路径的访问权限，控制哪些目录可以操作、哪些只能看。
- 只读：set_path_permission(path="D:\\重要项目", permission="READ_ONLY") → 可以查看，不能删除
- 禁止：set_path_permission(path="C:\\私人", permission="BLOCKED") → 完全不能访问
- 允许：set_path_permission(path="C:\\Windows", permission="ALLOW") → 解除限制

默认权限：系统目录（C:\Windows、C:\Program Files）只读，用户目录可读写。
用户说"XX目录只能看不能删"时，调用此工具设置 READ_ONLY。

## 注意事项
- 路径用反斜杠：C:\\Users\\Documents
- 优先使用专用工具，bash 作为后备
- 大文件清理建议需要用户确认
- 批量查询时用 fields 参数减少上下文浪费
- **关键：用户已经给出路径时，直接把完整路径传给 search_path / path 参数，不要传空值试探，不要逐级探索目录。**
"""


def create_agent():
    model = ChatAnthropic(
        model_name=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash"),
        base_url=os.environ["ANTHROPIC_BASE_URL"],
        api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
        temperature=0)
    checkpointer = MemorySaver()
    return create_deep_agent(
        model=model,
        tools=[search_files, find_duplicates, file_stats, file_metadata, set_path_permission, copy_files, move_files, delete_files, rename_file],
        system_prompt=SYSTEM_PROMPT,
        middleware=[ToolFilterMiddleware(), ObservabilityMiddleware(), ToolErrorMiddleware(), ToolPolicyMiddleware(), HumanConfirmMiddleware()],
        checkpointer = checkpointer
    )


def chat_loop(agent):
    """交互式对话循环。"""
    print("Bear Agent 已启动，输入 'quit' 退出")
    config = {"configurable": {"thread_id": "cli-session"}}
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if not user_input:
            continue
        try:
            print("\nAgent: ", end="", flush=True)
            stream = agent.stream_events(
                {"messages": [{"role": "user", "content": user_input}]},
                config=config,
                version="v3"
            )
            for message in stream.messages:
                print(message.text, end="", flush=True)
            print()
        except Exception as e:
            print(f"\n❌ 出错了: {e}")






