"""目录统计工具。

统计指定目录的文件数量、总大小、按扩展名/子目录/文件类型分类分布，以及最大文件 Top N。
底层采用降级链：Everything SDK（毫秒级）→ os.walk（兜底）。
"""
import logging
from collections import defaultdict
from pathlib import Path

from langchain_core.tools import tool

from backend.models import (
    FileStatsResult, SearchResult, ExtensionStats, DirStats,
    CategoryStats, LargestFile, SearchRequest,
)
from backend.services.search_engines import EverythingEngine, WalkEngine, build_query
from backend.utils.file_categories import classify_file

logger = logging.getLogger(__name__)

# 直接调用引擎（不走 FallbackSearcher，避免 ThreadPoolExecutor 额外开销）
_everything = EverythingEngine()
_walk = WalkEngine()

# Everything 用 0xFFFFFFFF（DWORD 最大值）表示无限制，拿完整数据
_EVERYTHING_MAX = 0xFFFFFFFF
_WALK_MAX = 50000


def _pct(size: int, total: int) -> float:
    """计算百分比，保留1位小数。total=0 时返回 0。"""
    if total == 0:
        return 0.0
    return round(size / total * 100, 1)


def _compute_stats(
    results: list[SearchResult],
    search_path: str,
    top_n: int = 20,
    largest_n: int = 10,
    truncated: bool = False,
) -> FileStatsResult:
    """在内存里对搜索结果做分组统计。

    Args:
        results: 搜索引擎返回的 SearchResult 列表。
        search_path: 用户指定的搜索根目录，用于取一级子目录名。
        top_n: 每个维度只返回前 N 个结果，其余归入"其他"。默认 20。
        largest_n: 返回最大的 N 个文件。默认 10。
        truncated: 搜索结果是否被截断（文件数达到 max_results 上限）。

    Returns:
        FileStatsResult，包含总览 + 按分类 + 按扩展名 + 按子目录 + 最大文件。
    """
    total_size = 0

    # 三个维度的聚合字典
    ext_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0})
    dir_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0, "path": ""})
    cat_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0, "exts": defaultdict(int)})

    for result in results:
        if result.size == 0:
            continue  # 跳过零字节文件（对统计无意义）

        total_size += result.size

        # 按扩展名统计
        ext = Path(result.name).suffix.lower().lstrip(".") or "无扩展名"
        ext_stats[ext]["count"] += 1
        ext_stats[ext]["size"] += result.size

        # 按一级子目录统计
        try:
            rel = Path(result.full_path).relative_to(search_path)
            dir_name = rel.parts[0] if len(rel.parts) > 1 else "根目录"
        except ValueError:
            dir_name = "其他"
        dir_stats[dir_name]["count"] += 1
        dir_stats[dir_name]["size"] += result.size
        dir_stats[dir_name]["path"] = str(Path(search_path) / dir_name)

        # 按文件类型分类统计（路径优先，扩展名兜底）
        category = classify_file(result.full_path, ext)
        cat_stats[category]["count"] += 1
        cat_stats[category]["size"] += result.size
        cat_stats[category]["exts"][ext] += result.size

    # --- 排序 ---
    sorted_ext = sorted(ext_stats.items(), key=lambda x: x[1]["size"], reverse=True)
    sorted_dir = sorted(dir_stats.items(), key=lambda x: x[1]["size"], reverse=True)
    sorted_cat = sorted(cat_stats.items(), key=lambda x: x[1]["size"], reverse=True)

    # --- 截断 ---
    top_ext = sorted_ext[:top_n]
    other_ext = sorted_ext[top_n:]
    top_dir = sorted_dir[:top_n]
    other_dir = sorted_dir[top_n:]

    # --- 按扩展名 ---
    by_extension = [
        ExtensionStats(
            extension=ext, count=v["count"], total_size=v["size"],
            percentage=_pct(v["size"], total_size),
        )
        for ext, v in top_ext
    ]

    # --- 按子目录 ---
    by_directory = [
        DirStats(
            name=name, path=v["path"], count=v["count"], total_size=v["size"],
            percentage=_pct(v["size"], total_size),
        )
        for name, v in top_dir
    ]

    # --- 按分类 ---
    by_category = [
        CategoryStats(
            category=cat, count=v["count"], total_size=v["size"],
            percentage=_pct(v["size"], total_size),
            top_extensions=sorted(v["exts"], key=lambda e: v["exts"][e], reverse=True)[:3],
        )
        for cat, v in sorted_cat
    ]

    # --- 最大文件 Top N ---
    largest = sorted(results, key=lambda r: r.size, reverse=True)[:largest_n]
    largest_files = [
        LargestFile(
            name=r.name, full_path=r.full_path, size=r.size,
            category=classify_file(r.full_path, Path(r.name).suffix.lower().lstrip(".")),
            modified=r.modified,
        )
        for r in largest if r.size > 0
    ]

    # --- other 桶 ---
    other_ext_size = sum(v["size"] for _, v in other_ext)
    other_dir_size = sum(v["size"] for _, v in other_dir)

    return FileStatsResult(
        path=search_path,
        file_count=len(results),
        dir_count=len(dir_stats),
        truncated=truncated,
        total_size=total_size,
        by_category=by_category,
        by_extension=by_extension,
        by_directory=by_directory,
        other_extensions_count=len(other_ext),
        other_extensions_size=other_ext_size,
        other_directories_count=len(other_dir),
        other_directories_size=other_dir_size,
        largest_files=largest_files,
    )


def _empty_result(path: str) -> FileStatsResult:
    """返回空的统计结果。"""
    return FileStatsResult(
        path=path, file_count=0, dir_count=0, truncated=False,
        total_size=0,
        by_category=[], by_extension=[], by_directory=[],
        other_extensions_count=0, other_extensions_size=0,
        other_directories_count=0, other_directories_size=0,
        largest_files=[],
    )


@tool("file_stats",description="统计目录概况工具")
def file_stats(
    path: str,
    max_depth: int | None = None,
    top_n: int = 20,
    largest_n: int = 10,
) -> FileStatsResult:
    """统计目录概况：文件数、总大小、按类型分类/扩展名/子目录分布，以及最大文件。

    用于了解一个目录的空间占用情况，是文件清理工作的第一步。
    底层采用降级链：优先 Everything（毫秒级），未安装时降级到 os.walk。

    Args:
        path: 要统计的目录路径。示例："D:\\Projects"、"C:\\Users"
        max_depth: 最大扫描深度。默认 None（不限制）。
            查看子目录概况时可设 1-3。分析整个磁盘空间时不要设置（保持 None）。
            设了会导致结果不完整。
        top_n: 每个维度只返回前 N 个结果，其余归入"其他"。默认 20。
        largest_n: 返回最大的 N 个文件。默认 10。设为 0 可不返回最大文件。

    Returns:
        FileStatsResult，包含：
        - file_count / dir_count: 文件数、子目录数
        - total_size: 总大小（字节）
        - by_category: 按文件类型分类（图片/视频/文档/代码/...），带百分比和 top_extensions
        - by_extension: 按扩展名分组，带百分比和平均文件大小
        - by_directory: 按一级子目录分组，带百分比
        - largest_files: 最大的 N 个文件，带分类标签
        - truncated: 数据是否被截断（文件数达到上限，total_size 可能不完整）
    """
    if not Path(path).exists():
        logger.warning("路径 %s 不存在", path)
        return _empty_result(path)

    logger.info("开始统计目录: %s, max_depth=%s", path, max_depth)

    req = SearchRequest(query="size:>0", search_path=path, max_depth=max_depth)
    parsed = build_query(req)

    # 直接调用 Everything（无限制），失败则降级到 os.walk
    results = _everything.search(parsed, max_results=_EVERYTHING_MAX)
    if not results:
        logger.info("Everything 无结果，降级到 os.walk")
        results = _walk.search(parsed, max_results=_WALK_MAX, max_depth=max_depth)

    if not results:
        logger.warning("所有引擎均无结果: %s", path)
        return _empty_result(path)

    # 截断检测：Everything 无限制不会截断，只有 os.walk 降级才可能截断
    truncated = len(results) >= _WALK_MAX

    stats = _compute_stats(results, path, top_n, largest_n, truncated=truncated)
    logger.info(
        "统计 %s 完成: %d 个文件, %d 字节, %d 个分类, truncated=%s",
        path, stats.file_count, stats.total_size, len(stats.by_category), truncated,
    )
    return stats
