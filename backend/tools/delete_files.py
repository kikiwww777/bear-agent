"""批量删除文件工具。

通过 result_id 从缓存获取搜索结果，或直接传 sources 路径列表。
默认移到回收站（可恢复），设置 to_recycle_bin=False 可永久删除。
"""
import logging
from pathlib import Path

from langchain_core.tools import tool

from backend.models import FileOperationResult, FileOperationItem
from backend.utils.result_cache import get_result_cache
from backend.utils.format import format_size

logger = logging.getLogger(__name__)

# 模块级单例
_cache = get_result_cache()


@tool("delete_files", description="文件删除工具")
def delete_files(
    result_id: str | None = None,
    sources: list[str] | None = None,
    to_recycle_bin: bool = True,
) -> FileOperationResult:
    """批量删除文件。

    两种调用方式：
    1. 通过 result_id：配合 search_files / find_duplicates 使用
       search_files(query="*.tmp") → delete_files(result_id="...")
    2. 通过 sources：直接传文件路径列表
       delete_files(sources=["D:\\a.tmp", "D:\\b.tmp"])

    默认移到回收站（可恢复）。设置 to_recycle_bin=False 可永久删除。

    Args:
        result_id: 搜索结果句柄（从 search_files / find_duplicates 获取）
        sources: 文件路径列表（直接指定要删除的文件）
        to_recycle_bin: True 移到回收站（默认），False 永久删除

    Returns:
        FileOperationResult，包含操作统计和失败详情
    """
    # 1. 获取文件列表
    if result_id:
        results = _cache.get(result_id)
        if results is None:
            logger.warning("结果句柄不存在或已过期: %s", result_id)
            return FileOperationResult(
                total_files=0, total_size=0,
                success_count=0, fail_count=0,
                failed_files=[],
            )
    elif sources:
        from backend.models import SearchResult
        results = []
        for path in sources:
            p = Path(path)
            if p.exists():
                stat = p.stat()
                results.append(SearchResult(
                    name=p.name,
                    path=str(p.parent),
                    full_path=str(p),
                    size=stat.st_size,
                    modified="",
                ))
    else:
        logger.warning("必须传 result_id 或 sources 参数")
        return FileOperationResult(
            total_files=0, total_size=0,
            success_count=0, fail_count=0,
            failed_files=[],
        )

    if not results:
        logger.warning("搜索结果为空: %s", result_id)
        return FileOperationResult(
            total_files=0, total_size=0,
            success_count=0, fail_count=0,
            failed_files=[],
        )

    # 2. 删除文件
    success_count = 0
    fail_count = 0
    failed_files = []
    total_size = 0
    mode = "回收站" if to_recycle_bin else "永久"
    logger.info("删除模式: %s, 文件数: %d", mode, len(results))

    for result in results:
        source = Path(result.full_path)

        try:
            if to_recycle_bin:
                import send2trash
                send2trash.send2trash(str(source))
            else:
                source.unlink()

            success_count += 1
            total_size += result.size
            logger.debug("删除成功: %s", source)
        except Exception as e:
            fail_count += 1
            failed_files.append(FileOperationItem(
                source=result.full_path,
                target=None,
                size=result.size,
                status="error",
                error=str(e),
            ))
            logger.warning("删除失败: %s | %s", source, e)

    # 3. 返回结果
    logger.info(
        "删除完成: %d 个文件, 成功 %d, 失败 %d, 总大小 %s, 模式: %s",
        len(results), success_count, fail_count, format_size(total_size), mode,
    )
    return FileOperationResult(
        total_files=len(results),
        total_size=total_size,
        success_count=success_count,
        fail_count=fail_count,
        failed_files=failed_files,
    )
