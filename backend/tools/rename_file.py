"""文件重命名工具。

重命名单个文件或目录。new_name 只接受文件名，不含路径分隔符，防止路径穿越。
"""
import logging
from pathlib import Path

from langchain_core.tools import tool

from backend.models import RenameResult

logger = logging.getLogger(__name__)


@tool("rename_file", description="文件重命名工具")
def rename_file(
    path: str,
    new_name: str,
) -> RenameResult:
    """重命名单个文件或目录。

    Args:
        path: 要重命名的文件或目录路径。示例：'D:\\docs\\old_name.pdf'
        new_name: 新的文件名（不含路径）。示例：'new_name.pdf'
            不允许包含路径分隔符（\\ 或 /），防止路径穿越。

    Returns:
        RenameResult，包含原路径、新路径和操作状态
    """
    source = Path(path)

    # 检查 new_name 不含路径分隔符
    if "\\" in new_name or "/" in new_name:
        msg = f"new_name 不允许包含路径分隔符，请只传文件名: {new_name}"
        logger.warning(msg)
        return RenameResult(
            old_path=str(source),
            new_path="",
            success=False,
            error=msg,
        )

    # 检查源文件是否存在
    if not source.exists():
        msg = f"源路径不存在: {path}"
        logger.warning(msg)
        return RenameResult(
            old_path=str(source),
            new_path="",
            success=False,
            error=msg,
        )

    # 构造目标路径
    target = source.parent / new_name

    # 检查目标是否已存在
    if target.exists():
        msg = f"目标路径已存在: {target}"
        logger.warning(msg)
        return RenameResult(
            old_path=str(source),
            new_path=str(target),
            success=False,
            error=msg,
        )

    # 执行重命名
    try:
        source.rename(target)
        logger.info("重命名成功: %s -> %s", source, target)
        return RenameResult(
            old_path=str(source),
            new_path=str(target),
            success=True,
        )
    except PermissionError as e:
        msg = f"权限不足，无法重命名: {path} | {e}"
        logger.warning(msg)
        return RenameResult(
            old_path=str(source),
            new_path=str(target),
            success=False,
            error=msg,
        )
    except OSError as e:
        msg = f"重命名失败: {path} | {e}"
        logger.warning(msg)
        return RenameResult(
            old_path=str(source),
            new_path=str(target),
            success=False,
            error=msg,
        )
