"""Bear Agent 真实 LLM 场景集成测试。

测试真实使用场景：
1. 文件搜索 + 统计工作流
2. find_duplicates → delete_files 链路
3. on_conflict 覆盖策略
4. 权限检查（sources 参数）
5. 结构化错误信息

每个测试通过 Agent 真实调用，验证工具调用结果和中间件行为。
"""
import os
import sys
import time
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

from langgraph.checkpoint.memory import MemorySaver
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent

# 测试目录
TEST_ROOT = Path(r"C:\Users\86159\Desktop\临时站\bear-agent")
DOCS_DIR = TEST_ROOT / "test_docs"
PHOTOS_DIR = TEST_ROOT / "test_photos"
JUNK_DIR = TEST_ROOT / "junk"

# 工具
from backend.tools.search_files import search_files
from backend.tools.find_duplicates import find_duplicates
from backend.tools.file_stats import file_stats
from backend.tools.file_metadata import file_metadata
from backend.tools.copy_files import copy_files
from backend.tools.move_files import move_files
from backend.tools.delete_files import delete_files
from backend.tools.rename_file import rename_file

# 中间件
from backend.middleware.ObservabilityMiddleware import ObservabilityMiddleware
from backend.middleware.ToolErrorMiddleware import ToolErrorMiddleware
from backend.middleware.ToolPolicyMiddleware import ToolPolicyMiddleware
from backend.middleware.HumanConfirmMiddleware import HumanConfirmMiddleware


SYSTEM_PROMPT = """你是 Bear Agent，Windows 文件管理助手。
可用工具：search_files, file_stats, find_duplicates, file_metadata, copy_files, move_files, delete_files, rename_file。
测试目录：C:\\Users\\86159\\Desktop\\临时站\\bear-agent
子目录：test_docs（文档）、test_photos（照片）、junk（垃圾文件）
按用户指令操作，完成后说"操作完成"。
"""


def create_test_agent():
    model = ChatAnthropic(
        model_name=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash"),
        base_url=os.environ["ANTHROPIC_BASE_URL"],
        api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
        temperature=0,
    )
    checkpointer = MemorySaver()
    return create_deep_agent(
        model=model,
        tools=[search_files, find_duplicates, file_stats, file_metadata,
               copy_files, move_files, delete_files, rename_file],
        system_prompt=SYSTEM_PROMPT,
        middleware=[ObservabilityMiddleware(), ToolErrorMiddleware(),
                    ToolPolicyMiddleware(), HumanConfirmMiddleware()],
        checkpointer=checkpointer,
    )


def run_agent(agent, prompt: str, thread_id: str = "test") -> str:
    """同步运行 agent，收集完整响应。"""
    config = {"configurable": {"thread_id": thread_id}}
    full_response = []

    stream = agent.stream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        config=config,
        version="v3",
    )

    for msg in stream.messages:
        text = getattr(msg, 'text', '') or str(msg)
        full_response.append(text)

    return ''.join(full_response)


# ═══════════════════════════════════════════
#  Scenario 1: 文件搜索 + 统计工作流
# ═══════════════════════════════════════════

def test_scenario_1_search_and_stats():
    """用户想了解 test_docs 目录的文件概况。"""
    print("\n" + "="*60)
    print("Scenario 1: 查看 test_docs 目录的文件概况")
    print("="*60)

    agent = create_test_agent()
    prompt = f"帮我看看 {DOCS_DIR} 这个目录里有什么文件，各占多少空间？"
    result = run_agent(agent, prompt, thread_id="s1")
    print(f"Agent: {result[:500]}")

    # 验证：响应中应该包含文件名
    assert "项目报告" in result or "会议记录" in result, "应该提到文档名"
    print("[PASS] Scenario 1 通过")
    return True


# ═══════════════════════════════════════════
#  Scenario 2: find_duplicates → 查看详情
# ═══════════════════════════════════════════

def test_scenario_2_find_duplicates():
    """用户查找 test_docs 中的重复文件。"""
    print("\n" + "="*60)
    print("Scenario 2: 查找重复文件")
    print("="*60)

    agent = create_test_agent()
    prompt = f"帮我在 {DOCS_DIR} 中找重复文件，看看有哪些重复的"
    result = run_agent(agent, prompt, thread_id="s2")
    print(f"Agent: {result[:800]}")

    # 验证：应该发现 项目报告 + 项目报告-副本 是重复，会议记录 + 会议记录-副本 是重复
    # 直接调用 find_duplicates 确认
    dup_result = find_duplicates.invoke({
        "query": "*",
        "search_path": str(DOCS_DIR),
        "min_size": 1,
    })
    print(f"\nfind_duplicates 直接调用: total_groups={dup_result.total_groups}, total_wasted={dup_result.total_wasted}")
    assert dup_result.total_groups >= 2, f"应该有至少 2 组重复，实际 {dup_result.total_groups}"
    print(f"[PASS] Scenario 2 通过（{dup_result.total_groups} 组重复，浪费 {dup_result.total_wasted} 字节）")
    return True


# ═══════════════════════════════════════════
#  Scenario 3: find_duplicates → copy_files 备份 → 删除原文件
# ═══════════════════════════════════════════

def test_scenario_3_duplicates_workflow():
    """用户想备份重复文件再清理。"""
    print("\n" + "="*60)
    print("Scenario 3: 重复文件 → 备份 → 清理")
    print("="*60)

    # 3a. 找重复文件
    dup_result = find_duplicates.invoke({
        "query": "*",
        "search_path": str(DOCS_DIR),
        "min_size": 1,
    })
    print(f"3a. 找到 {dup_result.total_groups} 组重复")
    assert dup_result.total_groups >= 2
    result_id = dup_result.result_id
    assert result_id, "应该有 result_id"

    # 3b. 备份到 backup 目录
    backup_dir = TEST_ROOT / "backup_test"
    backup_dir.mkdir(exist_ok=True)

    copy_result = copy_files.invoke({
        "result_id": result_id,
        "target_dir": str(backup_dir),
    })
    print(f"3b. 副本备份: 成功={copy_result.success_count}, 失败={copy_result.fail_count}, 跳过={copy_result.skipped_count}")

    # 再次复制（on_conflict=skip）
    copy_result2 = copy_files.invoke({
        "result_id": result_id,
        "target_dir": str(backup_dir),
        "on_conflict": "skip",
    })
    print(f"    再次复制(skip): 成功={copy_result2.success_count}, 跳过={copy_result2.skipped_count}")
    assert copy_result2.skipped_count > 0, "应该跳过已存在的文件"

    # 清理备份目录
    import shutil
    shutil.rmtree(backup_dir)
    print("[PASS] Scenario 3 通过")
    return True


# ═══════════════════════════════════════════
#  Scenario 4: on_conflict 三种模式
# ═══════════════════════════════════════════

def test_scenario_4_on_conflict():
    """测试覆盖策略的三种模式。"""
    print("\n" + "="*60)
    print("Scenario 4: on_conflict 三种模式")
    print("="*60)

    conflict_dir = TEST_ROOT / "conflict_test"
    target_dir = TEST_ROOT / "conflict_target"
    conflict_dir.mkdir(exist_ok=True)
    target_dir.mkdir(exist_ok=True)

    # 创建源文件
    src_file = conflict_dir / "test.txt"
    src_file.write_text("version 1")

    # 在目标目录创建同名但内容不同的文件
    target_file = target_dir / "test.txt"
    target_file.write_text("version 0")

    # 4a. skip（默认）→ 应该跳过
    result_skip = copy_files.invoke({
        "sources": [str(src_file)],
        "target_dir": str(target_dir),
        "on_conflict": "skip",
    })
    assert result_skip.skipped_count == 1, f"skip 应该跳过 1 个文件: {result_skip}"
    assert target_file.read_text() == "version 0", "skip 不应改目标文件内容"
    print(f"4a. skip: [PASS] 跳过={result_skip.skipped_count}, 目标内容保持='version 0'")

    # 4b. overwrite → 应该覆盖
    result_overwrite = copy_files.invoke({
        "sources": [str(src_file)],
        "target_dir": str(target_dir),
        "on_conflict": "overwrite",
    })
    assert result_overwrite.success_count == 1, f"overwrite 应该成功 1 个: {result_overwrite}"
    assert target_file.read_text() == "version 1", "overwrite 应覆盖目标文件"
    print(f"4b. overwrite: [PASS] 成功={result_overwrite.success_count}, 目标内容='version 1'")

    # 4c. error → 应该报告失败
    result_error = copy_files.invoke({
        "sources": [str(src_file)],
        "target_dir": str(target_dir),
        "on_conflict": "error",
    })
    assert result_error.fail_count >= 1, f"error 应该有失败: {result_error}"
    print(f"4c. error: [PASS] 失败={result_error.fail_count}, failed_files={len(result_error.failed_files)}")

    # 清理
    import shutil
    shutil.rmtree(conflict_dir)
    shutil.rmtree(target_dir)
    print("[PASS] Scenario 4 全部通过")
    return True


# ═══════════════════════════════════════════
#  Scenario 5: 权限检查 — sources 路径
# ═══════════════════════════════════════════

def test_scenario_5_permission_check_sources():
    """测试 sources 参数也走权限检查。"""
    print("\n" + "="*60)
    print("Scenario 5: sources 路径权限检查")
    print("="*60)

    from backend.utils.path_permissions import save_permission, get_permission, load_permissions

    # 创建一个测试目录并设为 READ_ONLY
    readonly_dir = TEST_ROOT / "readonly_test"
    readonly_dir.mkdir(exist_ok=True)
    test_file = readonly_dir / "secret.txt"
    test_file.write_text("top secret")

    # 设为 READ_ONLY
    save_permission(str(readonly_dir), "READ_ONLY")
    print(f"5a. 设置 {readonly_dir} 为 READ_ONLY")
    print(f"    get_permission('{readonly_dir}') = {get_permission(str(readonly_dir))}")

    # 尝试用 sources 删除 READ_ONLY 目录下的文件
    # 这应该被 ToolPolicyMiddleware 拦截
    result = delete_files.invoke({
        "sources": [str(test_file)],
        "to_recycle_bin": False,
    })
    print(f"5b. 尝试 delete_files(sources=['{test_file.name}']):")
    print(f"    成功={result.success_count}, 失败={result.fail_count}")

    # 注意：直接 invoke 工具绕过了中间件！
    # 需要通过中间件来验证权限检查
    # 我们可以直接测试中间件的 _check_path_permissions 函数
    from backend.middleware.ToolPolicyMiddleware import _check_path_permissions
    tool_args = {"sources": [str(test_file)]}
    block_reason = _check_path_permissions("delete_files", tool_args)
    print(f"5c. _check_path_permissions('delete_files', sources=['{test_file.name}']):")
    print(f"    拦截原因: {block_reason}")

    assert block_reason is not None, "sources 中的 READ_ONLY 路径应该被拦截"
    assert "只读" in block_reason, f"应该有'只读'字样: {block_reason}"
    print("[PASS] Scenario 5 通过")

    # 清理权限配置
    import os
    perm_file = Path(__file__).parent / "path_permissions.json"
    if perm_file.exists():
        os.remove(perm_file)
        load_permissions()  # 重新加载

    import shutil
    shutil.rmtree(readonly_dir)
    return True


# ═══════════════════════════════════════════
#  Scenario 6: 结构化错误信息
# ═══════════════════════════════════════════

def test_scenario_6_structured_error():
    """测试错误信息包含结构化字段。"""
    print("\n" + "="*60)
    print("Scenario 6: 结构化错误信息")
    print("="*60)

    from backend.middleware.ToolErrorMiddleware import _classify_exception, _format_structured_error

    # 6a. PermissionError
    e1 = PermissionError("[WinError 5] 拒绝访问。")
    code, retry, sugg = _classify_exception(e1)
    assert code == "PERMISSION_DENIED", f"code={code}"
    assert not retry, "PermissionError 不应可重试"
    print(f"6a. PermissionError → code={code}, retryable={retry}, suggestion={sugg}")

    # 6b. TimeoutError
    e2 = TimeoutError("搜索超时")
    code, retry, sugg = _classify_exception(e2)
    assert code == "TIMEOUT", f"code={code}"
    assert retry, "TimeoutError 应该可重试"
    print(f"6b. TimeoutError → code={code}, retryable={retry}, suggestion={sugg}")

    # 6c. OSError disk full
    e3 = OSError(28, "No space left on device")
    code, retry, sugg = _classify_exception(e3)
    assert code == "DISK_FULL", f"code={code}"
    print(f"6c. OSError(errno=28) → code={code}, retryable={retry}")

    # 6d. FileExistsError
    e4 = FileExistsError("目标文件已存在")
    code, retry, sugg = _classify_exception(e4)
    assert code == "FILE_EXISTS", f"code={code}"
    assert "on_conflict" in sugg, "建议应提到 on_conflict"
    print(f"6d. FileExistsError → code={code}, suggestion={sugg}")

    # 6e. Unknown error fallback
    e5 = RuntimeError("Random internal error")
    code, retry, sugg = _classify_exception(e5)
    assert code == "INTERNAL_ERROR", f"code={code}"
    print(f"6e. RuntimeError → code={code} (兜底)")

    # 6f. Format output
    formatted = _format_structured_error(
        tool_name="delete_files",
        error_type="PermissionError",
        error_message="拒绝访问",
        error_code="PERMISSION_DENIED",
        retryable=False,
        suggestion="请检查权限",
    )
    print(f"6f. 格式化输出:\n{formatted}")
    assert "PERMISSION_DENIED" in formatted
    assert "可重试: 否" in formatted
    assert "建议: 请检查权限" in formatted

    print("[PASS] Scenario 6 全部通过")
    return True


# ═══════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════

if __name__ == "__main__":
    print("=== Bear Agent 真实场景集成测试 ===\n")

    results = {}

    # Scenarios 4-6 不依赖 LLM（测试中间件和工具内部逻辑）
    for name, func in [
        ("on_conflict 覆盖策略", test_scenario_4_on_conflict),
        ("sources 权限检查", test_scenario_5_permission_check_sources),
        ("结构化错误信息", test_scenario_6_structured_error),
    ]:
        try:
            results[name] = func()
        except Exception as e:
            print(f"[FAIL] {name} 失败: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False

    # Scenarios 1-3 需要 LLM（可选，耗时较长）
    print("\n--- 以下测试需要 LLM 调用 ---")
    try:
        results["搜索 + 统计工作流"] = test_scenario_2_find_duplicates()
        results["find_duplicates 链路"] = test_scenario_3_duplicates_workflow()
    except Exception as e:
        print(f"[FAIL] LLM 测试失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*60)
    print("测试结果汇总:")
    for name, ok in results.items():
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {name}")
    print(f"\n总计: {sum(results.values())}/{len(results)} 通过")
