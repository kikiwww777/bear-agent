"""文件搜索工具。

支持按关键词、路径、扩展名、大小、时间等条件组合查询。
底层采用降级链：Everything SDK → os.walk，自动切换。

返回 Result Handle + Preview，避免上下文爆炸。

默认只返回文件，设置 include_dirs=True 可搜索文件夹。
"""
from pathlib import Path

from langchain_core.tools import tool

from backend.models import (
    SearchResult, SearchRequest, SearchFilesSchema,
    SearchResultHandle, SearchResultPage,
)
from backend.services.search_engines import FallbackSearcher, build_query
from backend.utils.result_cache import get_result_cache, PREVIEW_SIZE

# 模块级单例，避免每次调用都重建引擎实例
_searcher = FallbackSearcher()
_cache = get_result_cache()


@tool("search_files", args_schema=SearchFilesSchema,description="文件搜索工具")
def search_files(
    query: str = "*",
    search_path: str | None = None,
    extension: str | list[str] | None = None,
    min_size_mb: float | None = None,
    max_size_mb: float | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
    max_results: int = 100,
    max_depth: int | None = 10,
    result_id: str | None = None,
    page: int = 1,
    include_dirs: bool = False,
) -> SearchResultHandle | SearchResultPage:
    """文件搜索工具，支持模糊查询和精确条件组合搜索。

    两种调用方式：
    1. 执行新搜索：传 query 等搜索参数，返回 SearchResultHandle（句柄 + 预览）
    2. 获取分页结果：传 result_id 和 page 参数，返回 SearchResultPage

    Args:
        query: 搜索关键词，支持通配符。示例：'*.pdf'、'报告'、'*'
        search_path: 限定搜索目录。示例：'C:\\Users\\Documents'
        extension: 文件扩展名过滤。单个：'.pdf'，多个：['.jpg', '.png']
        min_size_mb: 最小文件大小（MB）
        max_size_mb: 最大文件大小（MB）
        modified_after: 修改时间下限，格式 YYYY-MM-DD
        modified_before: 修改时间上限，格式 YYYY-MM-DD
        max_results: 最大返回结果数，默认 100
        max_depth: 最大扫描目录深度，默认 10
        result_id: 结果句柄（获取分页结果时使用）
        page: 页码（获取分页结果时，默认 1）
        include_dirs: 是否包含目录，默认 False 只返回文件。搜索文件夹时设为 True。

    Returns:
        SearchResultHandle: 执行新搜索时，包含 result_id、preview、has_more
        SearchResultPage: 获取分页结果时，包含 results、page、has_more
    """
    # 模式 2：获取分页结果
    if result_id:
        results = _cache.get(result_id)
        if results is None:
            return SearchResultPage(
                result_id=result_id,
                results=[],
                page=page,
                has_more=False,
                total_files=0,
            )

        start = (page - 1) * PREVIEW_SIZE
        end = start + PREVIEW_SIZE
        page_results = results[start:end]

        return SearchResultPage(
            result_id=result_id,
            results=page_results,
            page=page,
            has_more=end < len(results),
            total_files=len(results),
        )

    # 模式 1：执行新搜索
    req = SearchRequest(
        query=query,
        search_path=search_path,
        extension=extension,
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        modified_after=modified_after,
        modified_before=modified_before,
        max_results=max_results,
        max_depth=max_depth,
    )

    parsed = build_query(req)
    results = _searcher.search(parsed, max_results=req.max_results, max_depth=req.max_depth)

    # 默认过滤掉目录，include_dirs=True 时保留
    if not include_dirs:
        filtered = []
        for r in results:
            try:
                if not Path(r.full_path).is_dir():
                    filtered.append(r)
            except (PermissionError, OSError):
                # 无法判断（权限不足等），排除掉，避免误删根目录等危险路径
                pass
        results = filtered

    # 生成 result_id 并缓存
    result_id = _cache.generate_id()
    _cache.put(result_id, results)

    # 计算总大小
    total_size = sum(r.size for r in results)

    # 返回预览
    preview = results[:PREVIEW_SIZE]

    return SearchResultHandle(
        result_id=result_id,
        total_files=len(results),
        preview=preview,
        has_more=len(results) > PREVIEW_SIZE,
    )
