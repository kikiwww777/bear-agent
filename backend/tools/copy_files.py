"""批量复制文件工具。

通过 result_id 从缓存获取搜索结果，然后复制到目标目录。
配合 search_files 使用：search_files 返回句柄，copy_files 通过句柄获取结果。
"""
import logging
import shutil
from pathlib import Path

from langchain_core.tools import tool

from backend.models import FileOperationResult, FileOperationItem
from backend.utils.result_cache import get_result_cache
from backend.utils.format import format_size

logger = logging.getLogger(__name__)

# 模块级单例
_cache = get_result_cache()


@tool("copy_files",description="文件复制工具")
def copy_files(
    target_dir: str,
    result_id: str | None = None,
    sources: list[str] | None = None,
) -> FileOperationResult:
    """批量复制文件到目标目录。

    两种调用方式：
    1. 通过 result_id：配合 search_files 使用
       search_files(query="*.pdf") → copy_files(result_id="...", target_dir="...")
    2. 通过 sources：直接传文件路径列表
       copy_files(sources=["D:\\a.pdf", "D:\\b.pdf"], target_dir="...")

    Args:
        target_dir: 目标目录路径
        result_id: 搜索结果句柄（从 search_files 获取）
        sources: 文件路径列表（直接指定要复制的文件）

    Returns:
        FileOperationResult，包含操作统计和失败详情
    """
    # 1. 获取文件列表
    if result_id:
        # 方式 1：从缓存获取搜索结果
        results = _cache.get(result_id)
        if results is None:
            logger.warning("结果句柄不存在或已过期: %s", result_id)
            return FileOperationResult(
                total_files=0, total_size=0,
                success_count=0, fail_count=0,
                failed_files=[],
            )
    elif sources:
        # 方式 2：从路径列表构造 SearchResult
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
                    modified="",  # 简化处理
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

    # 2. 创建目标目录
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    logger.info("目标目录: %s", target_dir)

    # 3. 复制文件
    success_count = 0
    fail_count = 0
    failed_files = []
    total_size = 0

    for result in results:
        source = Path(result.full_path)
        dest = target / result.name

        try:
            shutil.copy2(source, dest)
            success_count += 1
            total_size += result.size
            logger.debug("复制成功: %s -> %s", source, dest)
        except Exception as e:
            fail_count += 1
            failed_files.append(FileOperationItem(
                source=result.full_path,
                target=str(dest),
                size=result.size,
                status="error",
                error=str(e),
            ))
            logger.warning("复制失败: %s -> %s | %s", source, dest, e)

    # 4. 返回结果
    logger.info(
        "复制完成: %d 个文件, 成功 %d, 失败 %d, 总大小 %s",
        len(results), success_count, fail_count, format_size(total_size),
    )
    return FileOperationResult(
        total_files=len(results),
        total_size=total_size,
        success_count=success_count,
        fail_count=fail_count,
        failed_files=failed_files,
    )
