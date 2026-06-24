"""Bear Agent 工具策略中间件。

统一处理工具调用的输入验证和安全策略：
- 路径归一化：C:\\ → C:\，/ → \，合并连续反斜杠
- 输入净化：去掉控制字符、null 字节
- 路径权限检查：根据权限表决定是否放行（BLOCKED/READ_ONLY/ALLOW）
- 风险分级：SAFE / LOW / MEDIUM / HIGH / CRITICAL

使用方式：
    from backend.middleware.ToolPolicyMiddleware import ToolPolicyMiddleware

    agent = create_deep_agent(
        ...,
        middleware=[ToolPolicyMiddleware()],
    )

设计决策：
    - 路径归一化和输入净化从工具层统一收到中间件，新工具只需加一行映射
    - 路径权限使用 path_permissions 模块的可配置权限表，不再硬编码黑名单
    - BLOCKED 路径完全禁止访问，READ_ONLY 路径只允许 SAFE 工具（读操作）
    - 风险分级打标用于日志和未来 interrupt_on

执行顺序：
    ObservabilityMiddleware → ToolErrorMiddleware → ToolPolicyMiddleware → 工具执行
"""
import logging
import re
from typing import Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from backend.utils.path_permissions import get_permission

logger = logging.getLogger("bear_agent.tool_policy")


# ──────────────────────────────────────────────
#  参数映射：工具名 → 路径参数名 / 查询参数名
# ──────────────────────────────────────────────
#  新增工具时，只需在这里加一行映射，自动获得归一化 + 净化 + 安全拦截。

# 值是普通字符串的路径参数
TOOL_PATH_ARGS: dict[str, list[str]] = {
    "search_files": ["search_path"],
    "file_stats": ["path"],
    "find_duplicates": ["search_path"],
    "file_metadata": ["path"],
    "copy_files": ["target_dir"],
    "move_files": ["target_dir"],
    "rename_file": ["path"],
}

# 值是字符串列表的路径参数（如 file_metadata 的 paths）
TOOL_PATH_LIST_ARGS: dict[str, list[str]] = {
    "file_metadata": ["paths"],
    "delete_files": ["sources"],
}

# 查询参数（需要做输入净化）
TOOL_QUERY_ARGS: dict[str, list[str]] = {
    "search_files": ["query"],
}


# ──────────────────────────────────────────────
#  风险分级
# ──────────────────────────────────────────────
#  用于日志记录和未来 interrupt_on 配置。
#  Phase 4 添加执行工具时，需要在这里注册风险等级。

TOOL_RISK_LEVEL: dict[str, str] = {
    # 数据工具（只读，安全）
    "search_files": "SAFE",
    "file_stats": "SAFE",
    "find_duplicates": "SAFE",
    "file_metadata": "SAFE",
    # 执行工具
    "copy_files": "LOW",
    "move_files": "HIGH",
    "delete_files": "HIGH",
    "rename_file": "MEDIUM",
}


# ──────────────────────────────────────────────
#  路径归一化
# ──────────────────────────────────────────────

def normalize_path(path: str) -> tuple[str | None, str | None]:
    """归一化路径，返回 (归一化结果, 错误信息)。

    处理：
    - 路径穿越检查（..）
    - 正斜杠 → 反斜杠
    - 合并连续反斜杠

    Args:
        path: 原始路径

    Returns:
        成功：(normalized_path, None)
        失败：(None, error_message)
    """
    if ".." in path:
        return None, f"路径不允许包含 '..': {path}"

    # 正斜杠 → 反斜杠
    path = path.replace("/", "\\")

    # 合并连续反斜杠：C:\\ → C:\
    while "\\\\" in path:
        path = path.replace("\\\\", "\\")

    return path, None


# ──────────────────────────────────────────────
#  查询净化
# ──────────────────────────────────────────────

def sanitize_query(query: str) -> str:
    """净化查询字符串，去掉 null 字节和控制字符。

    处理：
    - 去掉 null 字节（\\x00）
    - 去掉控制字符（ASCII 0-31），保留 \\t \\n \\r
    - 去掉首尾空白
    - 空字符串返回 "*"

    Args:
        query: 原始查询字符串

    Returns:
        净化后的安全查询字符串
    """
    query = query.replace("\x00", "")
    query = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', query)
    query = query.strip()
    return query if query else "*"


# ──────────────────────────────────────────────
#  参数净化工具函数
# ──────────────────────────────────────────────

def _sanitize_path_args(
    tool_name: str,
    args: dict,
) -> str | None:
    """对单个路径参数做归一化，返回错误信息（无错误返回 None）。

    同时修改 args 中的值。
    """
    for arg_name in TOOL_PATH_ARGS.get(tool_name, []):
        value = args.get(arg_name)
        if not value:
            continue
        normalized, error = normalize_path(value)
        if error:
            logger.warning("路径归一化失败 | %s.%s | %s", tool_name, arg_name, error)
            return error
        args[arg_name] = normalized
    return None


def _sanitize_path_list_args(
    tool_name: str,
    args: dict,
) -> str | None:
    """对路径列表参数做归一化，返回错误信息（无错误返回 None）。

    同时修改 args 中的值。
    """
    for arg_name in TOOL_PATH_LIST_ARGS.get(tool_name, []):
        value = args.get(arg_name)
        if not value:
            continue
        normalized_paths = []
        for p in value:
            normalized, error = normalize_path(p)
            if error:
                logger.warning("路径归一化失败 | %s.%s | %s", tool_name, arg_name, error)
                return error
            normalized_paths.append(normalized)
        args[arg_name] = normalized_paths
    return None


def _sanitize_query_args(tool_name: str, args: dict) -> None:
    """对查询参数做净化，直接修改 args 中的值。"""
    for arg_name in TOOL_QUERY_ARGS.get(tool_name, []):
        value = args.get(arg_name)
        if value:
            args[arg_name] = sanitize_query(value)


def _check_path_permissions(tool_name: str, args: dict) -> str | None:
    """检查路径权限，返回拦截原因（放行返回 None）。

    权限逻辑：
    - BLOCKED：完全禁止，任何工具都不能访问
    - READ_ONLY：只读，只允许 SAFE 级别的工具（读操作）
    - ALLOW：放行

    Args:
        tool_name: 工具名
        args: 工具参数（已归一化）

    Returns:
        拦截原因字符串，或 None（放行）
    """
    risk = TOOL_RISK_LEVEL.get(tool_name, "UNKNOWN")

    for arg_name in TOOL_PATH_ARGS.get(tool_name, []):
        value = args.get(arg_name)
        if not value:
            continue

        permission = get_permission(value)

        if permission == "BLOCKED":
            reason = f"路径 '{value}' 被设置为完全禁止访问"
            logger.warning("路径权限拦截 | %s | %s", tool_name, reason)
            return reason

        if permission == "READ_ONLY" and risk != "SAFE":
            reason = f"路径 '{value}' 是只读的，'{tool_name}' 不是 SAFE 级别操作"
            logger.warning("路径权限拦截 | %s | %s", tool_name, reason)
            return reason

    return None


# ──────────────────────────────────────────────
#  错误消息模板
# ──────────────────────────────────────────────

_BLOCKED_MESSAGE_TEMPLATE = "权限拦截：{reason}。"


# ──────────────────────────────────────────────
#  中间件类
# ──────────────────────────────────────────────

class ToolPolicyMiddleware(AgentMiddleware):
    """工具策略中间件，统一处理输入验证和安全策略。

    功能：
    - 路径归一化：C:\\\\Users → C:\\Users，/ → \\
    - 输入净化：去掉 null 字节、控制字符
    - 路径权限检查：根据权限表决定是否放行
    - 风险分级：记录工具风险等级日志

    钩子：wrap_tool_call

    执行顺序：
        ObservabilityMiddleware → ToolErrorMiddleware → ToolPolicyMiddleware → 工具执行

    新增工具时：
        在 TOOL_PATH_ARGS / TOOL_PATH_LIST_ARGS / TOOL_QUERY_ARGS 中加一行映射即可。
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """包裹工具调用，做输入验证和安全策略检查。

        流程：
        1. 提取工具名和参数（复制一份，不改原始 request）
        2. 路径归一化（单个路径 + 路径列表）
        3. 查询净化
        4. 路径权限检查（BLOCKED 完全拦截，READ_ONLY 拦截非 SAFE 操作）
        5. 风险分级日志
        6. 写回净化后的参数，调用 handler

        Args:
            request: 工具调用请求
            handler: 执行工具的函数，必须调用

        Returns:
            工具执行结果，或安全拦截的错误消息
        """
        tool_name = request.tool_call.get("name", "unknown")
        tool_call_id = request.tool_call.get("id", "")

        # 复制参数，不改原始 request
        clean_args = dict(request.tool_call.get("args", {}))

        # ── 1. 路径归一化（单个路径）──
        error = _sanitize_path_args(tool_name, clean_args)
        if error:
            return ToolMessage(content=error, tool_call_id=tool_call_id, status="error")

        # ── 2. 路径归一化（路径列表）──
        error = _sanitize_path_list_args(tool_name, clean_args)
        if error:
            return ToolMessage(content=error, tool_call_id=tool_call_id, status="error")

        # ── 3. 查询净化 ──
        _sanitize_query_args(tool_name, clean_args)

        # ── 4. 路径权限检查 ──
        block_reason = _check_path_permissions(tool_name, clean_args)
        if block_reason:
            return ToolMessage(
                content=_BLOCKED_MESSAGE_TEMPLATE.format(reason=block_reason),
                tool_call_id=tool_call_id,
                status="error",
            )

        # ── 5. 风险分级日志 ──
        risk = TOOL_RISK_LEVEL.get(tool_name, "UNKNOWN")
        logger.info("工具策略检查 | %s | 风险: %s | 通过", tool_name, risk)

        # ── 6. 写回净化后的参数，调用 handler ──
        request.tool_call["args"] = clean_args
        return handler(request)
