"""重复文件查找工具。

两轮筛选：
1. 按文件大小分组（通过 FallbackSearcher 拿全盘文件列表，降级链自动选择最快引擎）
2. 对大小相同的候选文件算 MD5 哈希，哈希相同才是真正重复

工具设计（Handle + Preview + Pagination 模式，与 search_files 对齐）：
- find_duplicates(request) → 新搜索，返回 DuplicateGroupHandle（句柄 + 前 20 组，含完整文件路径）
- find_duplicates(result_id, page) → 分页，返回 DuplicateGroupPage

缓存由 ResultCache 统一管理，不再使用脆弱的模块级全局变量。
"""
import hashlib
import logging
from pathlib import Path

from langchain_core.tools import tool

from backend.models import (
    DuplicateRequest, DuplicateResult, FindDuplicatesSchema,
    DuplicateGroupHandle, DuplicateGroupPage, SearchResult,
)
from backend.services.search_engines import FallbackSearcher, build_query
from backend.utils.result_cache import get_result_cache, PREVIEW_SIZE

logger = logging.getLogger(__name__)

# 读取文件算哈希时的块大小（64KB），避免大文件一次性读入内存
_HASH_BLOCK_SIZE = 65536

# 模块级单例
_searcher = FallbackSearcher()
_cache = get_result_cache()

# ============================================================
#  过滤规则：跳过无清理价值的缓存/系统/开发文件
# ============================================================

# 目录黑名单：路径中包含这些关键词的文件直接跳过（不区分大小写）
_SKIP_DIR_KEYWORDS = {
    # 浏览器缓存
    "cache", "code cache", "gpucache", "serviceworker",
    # Chrome / Edge 多配置文件结构（扩展、数据按 Profile 隔立，删了会坏掉）
    "chrome", "google chrome", "chromium", "microsoft edge",
    # 包管理 / 虚拟环境
    "node_modules", "__pycache__", ".venv", "venv",
    ".npm", ".yarn", ".pnpm", ".cargo", ".rustup",
    # 版本控制
    ".git", ".svn", ".hg",
    # Windows 系统
    "$recycle.bin", "system volume information", "prefetch",
    "winsxs", "installer",
    # IDE / 编辑器缓存
    ".idea", "__ivy__", ".gradle", ".cache",
}

# 文件名黑名单（不区分大小写）
_SKIP_FILENAMES = {
    "desktop.ini", "thumbs.db", ".ds_store",
    "__init__.py",  # Python 包初始化文件，空文件大量重复但不能删
    # 浏览器数据文件（每个 Profile 独立，不能共享也不能删）
    "login data", "login data for account", "web data", "cookies",
    "history", "bookmarks", "preferences", "secure preferences",
    "favicons", "top sites", "shortcuts", "network action predictor",
    "previews resource", "shared dictionary", "budgetdatabase",
    "transportsecurity", "affiliation database",
}

# 扩展名黑名单（不区分大小写）
_SKIP_EXTENSIONS = {
    # Windows 系统/事务文件
    "regtrans-ms", "tmcontainer", "pf", "etl", "blf",
    # Python 编译
    "pyc", "pyo",
    # 日志 / 临时
    "log", "tmp", "temp",
}


# ============================================================
#  内部函数：两轮筛选逻辑
# ============================================================

def _compute_md5(file_path: Path) -> str | None:
    """计算文件的 MD5 哈希值。

    分块读取（64KB/块），不会一次性加载整个文件到内存。

    Args:
        file_path: 文件完整路径。

    Returns:
        32 位十六进制哈希字符串，读取失败返回 None。
    """
    try:
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(_HASH_BLOCK_SIZE):
                md5.update(chunk)
        return md5.hexdigest()
    except (OSError, PermissionError) as e:
        logger.debug("MD5 计算失败: %s — %s", file_path, e)
        return None


def _should_skip(file_path: str) -> bool:
    """判断文件是否应该跳过（缓存/系统/开发文件，无清理价值）。

    Args:
        file_path: 文件完整路径。

    Returns:
        True 表示应跳过，False 表示保留。
    """
    path_lower = file_path.lower()

    # 目录黑名单：路径中包含关键词就跳过
    for keyword in _SKIP_DIR_KEYWORDS:
        if keyword in path_lower:
            return True

    # 文件名黑名单
    name = Path(file_path).name.lower()
    if name in _SKIP_FILENAMES:
        return True

    # 扩展名黑名单
    ext = Path(file_path).suffix.lower().lstrip(".")
    if ext in _SKIP_EXTENSIONS:
        return True

    return False


def _group_by_size(results: list[SearchResult]) -> dict[int, list[SearchResult]]:
    """第一轮筛选：按文件大小归组，只保留存在重复的组。

    大小不同的文件内容不可能相同，先按 size 过滤可以跳过 90%+ 的文件，
    大幅减少第二轮 MD5 哈希的 IO 开销。

    Args:
        results: 搜索引擎返回的文件列表，每个元素包含 size 和 full_path。

    Returns:
        key 为文件大小（字节），value 为同大小文件列表。只包含 count >= 2 的组。
    """
    groups: dict[int, list[SearchResult]] = {}
    skipped = 0
    for r in results:
        if _should_skip(r.full_path):
            skipped += 1
            continue
        groups.setdefault(r.size, []).append(r)

    if skipped:
        logger.debug("过滤掉 %d 个缓存/系统/开发文件", skipped)

    # 只保留有 2 个及以上文件的组
    return {size: items for size, items in groups.items() if len(items) >= 2}


def _build_result(size_groups: dict[int, list[SearchResult]]) -> list[DuplicateResult]:
    """第二轮筛选：对候选文件算 MD5，按 (size, hash) 二次归组。

    哈希相同 = 内容相同 = 真正重复。
    单个文件的哈希组（没有重复）会被丢弃。

    Args:
        size_groups: 第一轮的输出，key 为文件大小，value 为同大小文件列表。

    Returns:
        重复文件组列表，每组包含 hash、size、count、wasted 和文件详情。
        无重复时返回空列表。
    """
    results: list[DuplicateResult] = []

    for size, files in size_groups.items():
        # 按 (size, hash) 归组
        hash_groups: dict[tuple[int, str], list[SearchResult]] = {}
        for f in files:
            hash_value = _compute_md5(Path(f.full_path))
            if hash_value is None:
                continue
            hash_groups.setdefault((size, hash_value), []).append(f)

        # 筛掉哈希唯一的文件（不是真的重复）
        for (group_size, group_hash), group_files in hash_groups.items():
            if len(group_files) < 2:
                continue
            count = len(group_files)
            wasted = group_size * (count - 1)
            results.append(DuplicateResult(
                hash=group_hash,
                size=group_size,
                count=count,
                wasted=wasted,
                files=group_files,
            ))

    return results


def _build_handle(duplicates: list[DuplicateResult]) -> DuplicateGroupHandle:
    """将完整结果列表转为 Handle + Preview。

    Args:
        duplicates: 重复文件组列表。

    Returns:
        DuplicateGroupHandle，包含 result_id、前 20 组预览、分页信息。
    """
    # 按浪费空间降序排列
    duplicates.sort(key=lambda d: d.wasted, reverse=True)

    # 缓存完整结果
    result_id = _cache.generate_id(prefix="dup")
    _cache.put(result_id, duplicates)

    # 预览前 20 组
    preview = duplicates[:PREVIEW_SIZE]
    total_wasted = sum(d.wasted for d in duplicates)

    return DuplicateGroupHandle(
        result_id=result_id,
        total_groups=len(duplicates),
        total_wasted=total_wasted,
        preview=preview,
        has_more=len(duplicates) > PREVIEW_SIZE,
    )


# ============================================================
#  工具入口
# ============================================================

@tool("find_duplicates", args_schema=FindDuplicatesSchema,description="寻找重复文件工具")
def find_duplicates(
    query: str = "size:>0",
    search_path: str | None = None,
    extension: str | list[str] | None = None,
    min_size: int | None = 1024,
    min_size_mb: float | None = None,
    max_size_mb: float | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
    max_results: int = 10000,
    max_depth: int | None = None,
    result_id: str | None = None,
    page: int = 1,
) -> DuplicateGroupHandle | DuplicateGroupPage:
    """查找重复文件，支持新搜索和分页查询两种模式。

    模式 1 — 新搜索：传 search_path 等搜索参数，返回 DuplicateGroupHandle（句柄 + 前 20 组，含完整文件路径）。
    模式 2 — 分页查询：传 result_id 和 page 参数，返回 DuplicateGroupPage（更多组）。

    两轮筛选：先按大小分组，再按 MD5 哈希确认。
    自动选择最快搜索引擎（Everything 毫秒级 / os.walk 兜底）。
    结果按浪费空间降序排列，每组包含完整文件路径，可直接用于 copy_files/delete_files。

    Args:
        search_path: 限定搜索目录
        extension: 文件扩展名过滤
        min_size: 最小文件大小（字节），默认 1024
        min_size_mb: 最小文件大小（MB）
        max_size_mb: 最大文件大小（MB）
        modified_after: 修改时间下限，格式 YYYY-MM-DD
        modified_before: 修改时间上限，格式 YYYY-MM-DD
        max_results: 搜索范围上限（默认 10000）
        max_depth: 最大扫描目录深度
        result_id: 结果句柄（分页查询时使用）
        page: 页码（分页查询时，默认 1）

    Returns:
        模式 1: DuplicateGroupHandle，包含 result_id、total_groups、total_wasted、preview（前 20 组含文件路径）、has_more
        模式 2: DuplicateGroupPage，包含 results（含文件路径）、page、has_more、total_groups
    """
    # ── 模式 2：分页查询 ──
    if result_id:
        results = _cache.get(result_id)
        if results is None:
            logger.warning("结果句柄不存在或已过期: %s", result_id)
            return DuplicateGroupPage(
                result_id=result_id, results=[], page=page,
                has_more=False, total_groups=0,
            )

        start = (page - 1) * PREVIEW_SIZE
        end = start + PREVIEW_SIZE
        page_results = results[start:end]

        return DuplicateGroupPage(
            result_id=result_id,
            results=page_results,
            page=page,
            has_more=end < len(results),
            total_groups=len(results),
        )

    # ── 模式 1：新搜索 ──
    request = DuplicateRequest(
        query=query,
        search_path=search_path,
        extension=extension,
        min_size=min_size,
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        modified_after=modified_after,
        modified_before=modified_before,
        max_results=max_results,
        max_depth=max_depth,
    )

    parsed = build_query(request)
    logger.info("开始查找重复文件: path=%s, max_results=%d", parsed.search_path, request.max_results)

    # 第一轮：通过降级链拿文件列表，按大小归组
    results = _searcher.search(parsed, max_results=request.max_results)
    size_groups = _group_by_size(results)

    logger.info("第一轮完成: 共 %d 个候选组（大小相同的文件组）", len(size_groups))

    if not size_groups:
        logger.info("未找到大小相同的文件，无重复")
        return DuplicateGroupHandle(
            result_id="", total_groups=0, total_wasted=0,
            preview=[], has_more=False,
        )

    # 第二轮：对候选文件算 MD5，构造完整结果
    duplicates = _build_result(size_groups)

    if not duplicates:
        logger.info("MD5 筛选后无真正重复的文件")
        return DuplicateGroupHandle(
            result_id="", total_groups=0, total_wasted=0,
            preview=[], has_more=False,
        )

    # 构建 Handle + Preview，缓存完整结果
    handle = _build_handle(duplicates)

    logger.info("查找完成: 共 %d 组重复文件，可释放 %d 字节",
                handle.total_groups, handle.total_wasted)
    return handle
