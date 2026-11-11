"""Bear Agent 人类确认中间件。

基于多维度动态风险评估，在高风险操作前要求用户确认。
风险评估不仅看工具名，还考虑文件数量、数据量、可逆性等因素。

使用方式：
    from backend.middleware.HumanConfirmMiddleware import HumanConfirmMiddleware

    agent = create_deep_agent(
        ...,
        middleware=[HumanConfirmMiddleware()],
    )

风险评估维度：
    - 基础风险：来自 ToolPolicyMiddleware 的 TOOL_RISK_LEVEL
    - 文件数量：> 50 个文件风险 +1
    - 数据量：> 10GB 风险 +1
    - 可逆性：永久删除风险 +1
    - 系统目录：操作 C:\\Windows 等风险 +1
    - 单文件小操作：count == 1 且 size < 100MB 风险 -1

确认阈值：final_risk >= MEDIUM

执行顺序：
    ObservabilityMiddleware → ToolErrorMiddleware → ToolPolicyMiddleware → HumanConfirmMiddleware → 工具执行
"""
import logging
from pathlib import Path
from typing import Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from backend.utils.result_cache import get_result_cache, extract_files_from_results
from backend.utils.format import format_size

logger = logging.getLogger("bear_agent.human_confirm")

# 模块级单例
_cache = get_result_cache()


# ──────────────────────────────────────────────
#  风险等级定义（与 ToolPolicyMiddleware 对齐）
# ──────────────────────────────────────────────

RISK_SAFE = 0
RISK_LOW = 1
RISK_MEDIUM = 2
RISK_HIGH = 3
RISK_CRITICAL = 4

RISK_LEVEL_MAP = {
    "SAFE": RISK_SAFE,
    "LOW": RISK_LOW,
    "MEDIUM": RISK_MEDIUM,
    "HIGH": RISK_HIGH,
    "CRITICAL": RISK_CRITICAL,
}

RISK_NAMES = {v: k for k, v in RISK_LEVEL_MAP.items()}

# 基础风险等级（与 ToolPolicyMiddleware.TOOL_RISK_LEVEL 对齐）
TOOL_RISK_LEVEL: dict[str, str] = {
    "search_files": "SAFE",
    "file_stats": "SAFE",
    "find_duplicates": "SAFE",
    "file_metadata": "SAFE",
    "set_path_permission": "LOW",
    "copy_files": "LOW",
    "move_files": "HIGH",
    "delete_files": "HIGH",
    "rename_file": "MEDIUM",
}

# 确认阈值
CONFIRM_THRESHOLD = RISK_MEDIUM

# 系统目录列表
SYSTEM_DIRS = [
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
]


# ──────────────────────────────────────────────
#  文件信息收集
# ──────────────────────────────────────────────

def _get_file_info(args: dict) -> tuple[int, int]:
    """从工具参数获取文件数量和总大小。

    支持两种来源：
    - result_id：从缓存获取
    - sources：直接计算

    Returns:
        (file_count, total_size_bytes)
    """
    result_id = args.get("result_id")
    sources = args.get("sources")

    if result_id:
        raw = _cache.get(result_id)
        if raw:
            flat = extract_files_from_results(raw)
            return len(flat), sum(r.size for r in flat)

    if sources:
        count = 0
        total_size = 0
        for path in sources:
            try:
                p = Path(path)
                if p.exists():
                    count += 1
                    total_size += p.stat().st_size
            except (PermissionError, OSError):
                pass
        return count, total_size

    return 0, 0


def _check_target_exists(args: dict) -> bool:
    """检查目标路径是否已存在（可能被覆盖）。"""
    target_dir = args.get("target_dir")
    if target_dir:
        return Path(target_dir).exists()
    return False


def _check_system_dir(args: dict) -> bool:
    """检查操作目标是否在系统目录中。"""
    # 检查主要路径参数
    for key in ["search_path", "path", "target_dir"]:
        value = args.get(key)
        if value:
            normalized = value.replace("/", "\\")
            for sys_dir in SYSTEM_DIRS:
                if normalized.startswith(sys_dir):
                    return True

    # 检查 sources 列表
    sources = args.get("sources")
    if sources:
        for s in sources:
            normalized = s.replace("/", "\\")
            for sys_dir in SYSTEM_DIRS:
                if normalized.startswith(sys_dir):
                    return True

    return False


# ──────────────────────────────────────────────
#  风险评估
# ──────────────────────────────────────────────

def assess_risk(tool_name: str, args: dict) -> tuple[int, dict]:
    """评估工具调用的最终风险等级。

    Returns:
        (risk_level, factors) — factors 是各因子的详情，用于生成摘要
    """
    base_risk_str = TOOL_RISK_LEVEL.get(tool_name, "SAFE")
    base_risk = RISK_LEVEL_MAP.get(base_risk_str, RISK_SAFE)
    factors = {"base": base_risk_str}

    # SAFE 直接返回
    if base_risk == RISK_SAFE:
        return RISK_SAFE, factors

    # 收集动态因子
    file_count, total_size = _get_file_info(args)
    is_permanent = not args.get("to_recycle_bin", True)
    target_exists = _check_target_exists(args)
    is_system_dir = _check_system_dir(args)

    factors["file_count"] = file_count
    factors["total_size"] = total_size
    factors["is_permanent"] = is_permanent
    factors["target_exists"] = target_exists
    factors["is_system_dir"] = is_system_dir

    # 计算最终风险
    risk = base_risk

    # 动态提升
    if file_count > 50:
        risk += 1
        factors["count_boost"] = True
    if total_size > 10 * 1024 * 1024 * 1024:  # 10GB
        risk += 1
        factors["size_boost"] = True
    if is_permanent:
        risk += 1
        factors["permanent_boost"] = True
    if target_exists:
        risk += 1
        factors["overwrite_boost"] = True
    if is_system_dir:
        risk += 1
        factors["system_dir_boost"] = True

    # 动态降级
    if file_count == 1 and total_size < 100 * 1024 * 1024:  # 1 文件 < 100MB
        risk -= 1
        factors["small_op_discount"] = True

    # 限制范围
    risk = max(RISK_SAFE, min(RISK_CRITICAL, risk))

    return risk, factors


# ──────────────────────────────────────────────
#  确认 UI
# ──────────────────────────────────────────────

# 工具名到用户友好描述的映射
_TOOL_DESCRIPTIONS = {
    "delete_files": "删除",
    "move_files": "移动",
    "copy_files": "复制",
    "rename_file": "重命名",
}

# 最多显示的文件名数量
_MAX_DISPLAY_FILES = 20


def _get_file_samples(args: dict) -> list[str]:
    """获取要操作的文件名样本（最多 _MAX_DISPLAY_FILES 个）。

    支持两种来源：
    - result_id：从缓存获取
    - sources：直接使用
    """
    result_id = args.get("result_id")
    sources = args.get("sources")

    if result_id:
        raw = _cache.get(result_id)
        if raw:
            flat = extract_files_from_results(raw)
            return [r.name for r in flat[:_MAX_DISPLAY_FILES]]

    if sources:
        return [Path(s).name for s in sources[:_MAX_DISPLAY_FILES]]

    return []


def _get_source_dir(args: dict) -> str | None:
    """获取文件来源目录。"""
    sources = args.get("sources")
    if sources and len(sources) > 0:
        return str(Path(sources[0]).parent)

    result_id = args.get("result_id")
    if result_id:
        raw = _cache.get(result_id)
        if raw:
            flat = extract_files_from_results(raw)
            if flat:
                return str(Path(flat[0].full_path).parent)

    return None


def _build_summary(tool_name: str, risk_level: int, factors: dict, args: dict) -> str:
    """构建操作摘要，展示给用户确认。

    设计原则：
    - 不显示工具名（用户不需要知道 delete_files）
    - 用自然语言描述操作
    - 突出关键信息：文件数、大小、是否可逆
    - 显示文件名样本，让用户知道要操作什么
    - 显示来源/目标目录，让用户知道操作上下文
    """
    action = _TOOL_DESCRIPTIONS.get(tool_name, tool_name)
    lines = []

    # 获取文件名样本和目录信息
    file_samples = _get_file_samples(args)
    source_dir = _get_source_dir(args)
    target_dir = args.get("target_dir")

    # 主操作描述
    file_count = factors.get("file_count", 0)
    total_size = factors.get("total_size", 0)
    size_str = format_size(total_size) if total_size > 0 else ""

    # 第一行：操作概述
    if file_count > 0:
        if factors.get("is_permanent"):
            lines.append(f"⚠️  即将永久删除 {file_count} 个文件（{size_str}，不可恢复）")
        else:
            lines.append(f"⚠️  即将{action} {file_count} 个文件（{size_str}）")
    else:
        lines.append(f"⚠️  即将{action}文件")

    # 第二行：来源/目标目录
    if tool_name in ("move_files", "copy_files") and target_dir:
        lines.append(f"   从：{source_dir or '未知'}")
        lines.append(f"   到：{target_dir}")
    elif source_dir:
        lines.append(f"   位置：{source_dir}")

    # 第三行：文件名样本
    if file_samples:
        lines.append(f"   文件：{', '.join(file_samples)}")
        if file_count > _MAX_DISPLAY_FILES:
            lines.append(f"   ...等共 {file_count} 个文件")

    # 额外警告
    if factors.get("target_exists"):
        lines.append(f"   ⚠️ 目标目录已存在，可能覆盖同名文件")

    if factors.get("is_system_dir"):
        lines.append(f"   ⚠️ 操作涉及系统目录，请谨慎确认")

    lines.append(f"\n确认执行吗？(y/n)")

    return "\n".join(lines)


def _ask_user_confirmation(summary: str) -> bool:
    """展示摘要并等待用户确认。"""
    print(f"\n{summary}")
    print("  > ", end="", flush=True)
    try:
        answer = input().strip().lower()
        return answer in ("y", "yes", "是")
    except (EOFError, KeyboardInterrupt):
        return False


# ──────────────────────────────────────────────
#  中间件类
# ──────────────────────────────────────────────

class HumanConfirmMiddleware(AgentMiddleware):
    """人类确认中间件，基于多维度动态风险评估。

    功能：
    - 多维度风险评估（工具基础风险 + 文件数量 + 数据量 + 可逆性 + 系统目录）
    - 超过阈值时展示操作摘要，等待 CLI 用户确认
    - 用户拒绝时返回结构化错误，Agent 可以换种方式尝试

    钩子：wrap_tool_call

    执行顺序：
        ObservabilityMiddleware → ToolErrorMiddleware → ToolPolicyMiddleware → HumanConfirmMiddleware → 工具执行
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """包裹工具调用，评估风险并决定是否需要确认。

        流程：
        1. 提取工具名和参数
        2. 评估风险等级
        3. 低于阈值直接放行
        4. 超过阈值展示摘要，等用户确认
        5. 用户确认 → 调用 handler
        6. 用户拒绝 → 返回错误消息

        Args:
            request: 工具调用请求
            handler: 执行工具的函数，必须调用

        Returns:
            工具执行结果，或用户拒绝的错误消息
        """
        tool_name = request.tool_call.get("name", "unknown")
        tool_args = request.tool_call.get("args", {})
        tool_call_id = request.tool_call.get("id", "")

        # 评估风险
        risk, factors = assess_risk(tool_name, tool_args)

        # 低于阈值直接放行
        if risk < CONFIRM_THRESHOLD:
            logger.debug(
                "风险评估 | %s | %s | 低于阈值，放行",
                tool_name, RISK_NAMES.get(risk),
            )
            return handler(request)

        # 超过阈值，需要确认
        logger.info(
            "风险评估 | %s | %s | 需要确认",
            tool_name, RISK_NAMES.get(risk),
        )

        summary = _build_summary(tool_name, risk, factors, tool_args)

        if _ask_user_confirmation(summary):
            logger.info("用户确认 | %s | 执行", tool_name)
            return handler(request)
        else:
            logger.info("用户拒绝 | %s | 取消", tool_name)
            return ToolMessage(
                content=f"用户取消了 {tool_name} 操作。",
                tool_call_id=tool_call_id,
                status="error",
            )
