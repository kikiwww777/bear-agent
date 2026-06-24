"""Bear Agent 数据模型定义。

所有 Pydantic 模型统一在此模块管理，供 tools 和 services 层共用。
"""
from typing import List

from pydantic import BaseModel, Field, model_validator


class SearchResult(BaseModel):
    """文件搜索结果。"""

    name: str = Field(description="文件名")
    path: str = Field(description="文件所在目录")
    full_path: str = Field(description="文件完整路径")
    size: int = Field(description="文件大小（字节）")
    modified: str = Field(description="最后修改时间，格式 YYYY-MM-DD HH:MM:SS")


class SearchRequest(BaseModel):
    """文件搜索请求参数。"""

    query: str = Field(
        description="搜索关键词，支持通配符。示例：'*.pdf'、'报告'、'*'"
    )
    search_path: str | None = Field(
        default=None,
        description="限定搜索目录。示例：'C:\\Users\\Documents'",
    )
    extension: str | List[str] | None = Field(
        default=None,
        description="文件扩展名过滤。单个：'.pdf'，多个：['.jpg', '.png']",
    )
    min_size_mb: float | None = Field(
        default=None,
        description="最小文件大小（MB）",
    )
    max_size_mb: float | None = Field(
        default=None,
        description="最大文件大小（MB）",
    )
    modified_after: str | None = Field(
        default=None,
        description="修改时间下限，格式 YYYY-MM-DD。示例：'2024-01-01'",
    )
    modified_before: str | None = Field(
        default=None,
        description="修改时间上限，格式 YYYY-MM-DD。示例：'2024-12-31'",
    )
    max_results: int = Field(
        default=100,
        description="最大返回结果数",
    )
    max_depth: int | None = Field(
        default=10,
        description="最大扫描目录深度（仅 os.walk 兜底引擎生效）。"
                    "None 表示不限制。普通搜索用 10，深层搜索用 20 或 None。",
    )

class SearchFilesSchema(SearchRequest):
    """search_files 工具的完整参数 schema。

    继承 SearchRequest 的所有搜索字段（query 变为可选），
    加上分页模式的 result_id 和 page。
    用于 @tool(args_schema=SearchFilesSchema) 让 LLM 直接传扁平参数。
    """
    query: str = Field(
        default="*",
        description="搜索关键词，支持通配符。示例：'*.pdf'、'报告'、'*'",
    )
    result_id: str | None = Field(
        default=None,
        description="结果句柄（获取分页结果时使用，从之前的搜索结果获取）",
    )
    page: int = Field(
        default=1,
        description="页码（获取分页结果时使用，默认 1）",
    )
    include_dirs: bool = Field(
        default=False,
        description="是否包含目录。默认 False 只返回文件，设为 True 可搜索文件夹。",
    )


class DuplicateResult(BaseModel):
    """重复文件组（包含完整文件列表和可释放空间）。

    find_duplicates 直接返回此模型，LLM 可以看到每个文件的完整路径。
    按浪费空间降序排列，前 20 组通过 Handle 预览，更多通过分页获取。
    """

    hash: str = Field(description="文件内容的 MD5 哈希值")
    size: int = Field(description="单个文件大小（字节）")
    count: int = Field(description="重复文件数量")
    wasted: int = Field(description="可释放空间（字节）= size × (count - 1)")
    files: List[SearchResult] = Field(description="重复文件列表")


class DuplicateGroupHandle(BaseModel):
    """重复文件组句柄。

    find_duplicates 返回句柄 + 前 20 组预览（含完整文件路径）。
    与 SearchResultHandle 设计对齐。
    """
    result_id: str = Field(description="结果句柄，传给 find_duplicates 分页查询")
    total_groups: int = Field(description="重复文件组总数")
    total_wasted: int = Field(description="可释放空间总和（字节）")
    preview: List[DuplicateResult] = Field(description="前 20 组重复文件（含完整路径）")
    has_more: bool = Field(description="是否有更多结果")


class DuplicateGroupPage(BaseModel):
    """重复文件组分页。

    用于获取 find_duplicates 返回的句柄的分页结果。
    与 SearchResultPage 设计对齐。
    """
    result_id: str = Field(description="结果句柄")
    results: List[DuplicateResult] = Field(description="当前页的重复文件组")
    page: int = Field(description="当前页码")
    has_more: bool = Field(description="是否有更多页")
    total_groups: int = Field(description="重复文件组总数")


class DuplicateRequest(SearchRequest):
    """重复文件查找请求参数。

    继承 SearchRequest 的过滤维度（search_path、extension、min_size_mb、
    max_size_mb、modified_after、modified_before），可按文件类型、时间范围等
    条件限定重复文件的搜索范围。

    示例：
        只查 PDF 重复：DuplicateRequest(extension=".pdf")
        只查最近一个月：DuplicateRequest(modified_after="2025-05-01")
        查 D 盘图片重复：DuplicateRequest(search_path="D:\\\\", extension=[".jpg", ".png"])
    """
    query: str = Field(
        default="size:>0",
        description="搜索关键词，默认 'size:>0' 过滤零字节文件",
    )
    min_size: int | None = Field(
        default=1024,
        description="最小文件大小（字节）。比 min_size_mb 优先级高",
    )
    max_results: int = Field(
        default=10000,
        description="最大返回文件数（控制搜索范围，不是结果组数）",
    )

    @model_validator(mode="after")
    def _sync_min_size(self) -> "DuplicateRequest":
        """min_size（字节）自动同步到 min_size_mb（MB），让 build_query 统一处理。
        只在 min_size >= 1MB 时同步，小于此值的过滤由 os.walk 引擎在内存中处理。
        Everything 的 size: 语法对极小值（如 0.001mb）支持不好。
        """
        if self.min_size and not self.min_size_mb:
            min_mb = self.min_size / (1024 * 1024)
            if min_mb >= 1:
                self.min_size_mb = min_mb
        return self

class FindDuplicatesSchema(DuplicateRequest):
    """find_duplicates 工具的完整参数 schema。

    继承 DuplicateRequest 的所有搜索字段，
    加上分页模式的 result_id 和 page。
    用于 @tool(args_schema=FindDuplicatesSchema) 让 LLM 直接传扁平参数。
    """
    result_id: str | None = Field(
        default=None,
        description="结果句柄（获取分页结果时使用）",
    )
    page: int = Field(
        default=1,
        description="页码（获取分页结果时使用，默认 1）",
    )


class ExtensionStats(BaseModel):
    """按文件扩展名统计。"""
    extension: str = Field(description="文件扩展名")
    count: int = Field(description="文件数量")
    total_size: int = Field(description="文件总大小（字节）")
    percentage: float = Field(description="占总大小的百分比（0-100，保留1位小数）")

class DirStats(BaseModel):
    """按目录统计。"""
    name: str = Field(description="目录名")
    path: str = Field(description="目录路径")
    count: int = Field(description="文件数量")
    total_size: int = Field(description="文件总大小（字节）")
    percentage: float = Field(description="占总大小的百分比（0-100，保留1位小数）")

class CategoryStats(BaseModel):
    """按文件类型分类统计（参照 Windows 存储感知分类）。"""
    category: str = Field(description="分类名：图片/视频/音乐/文档/压缩包/代码/安装包/临时文件/字体/磁盘镜像/其他")
    count: int = Field(description="文件数量")
    total_size: int = Field(description="文件总大小（字节）")
    percentage: float = Field(description="占总大小的百分比（0-100，保留1位小数）")
    top_extensions: List[str] = Field(
        default_factory=list,
        description="该分类下最大的 3 个扩展名，方便快速了解构成",
    )

class LargestFile(BaseModel):
    """单个大文件信息。"""
    name: str = Field(description="文件名")
    full_path: str = Field(description="完整路径")
    size: int = Field(description="大小（字节）")
    category: str = Field(description="所属分类")
    modified: str = Field(description="最后修改时间")

class FileStatsResult(BaseModel):
    """目录统计结果。"""
    path: str = Field(description="统计目录路径")
    file_count: int = Field(description="文件总数")
    dir_count: int = Field(description="子目录数")
    truncated: bool = Field(description="数据是否被截断（文件数达到 max_results 上限）")
    total_size: int = Field(description="文件总大小（字节）")
    by_category: List[CategoryStats] = Field(
        default_factory=list,
        description="按文件类型分类统计，按总大小降序排列",
    )
    by_extension: List[ExtensionStats] = Field(
        default_factory=list,
        description="按扩展名统计，按总大小降序排列",
    )
    other_extensions_count: int = Field(description="被截断的扩展名种类数")
    other_extensions_size: int = Field(description="被截断的扩展名文件总大小（字节）")
    by_directory: List[DirStats] = Field(
        default_factory=list,
        description="按一级子目录统计，按总大小降序排列",
    )
    other_directories_count: int = Field(description="被截断的子目录数")
    other_directories_size: int = Field(description="被截断的子目录文件总大小（字节）")
    largest_files: List[LargestFile] = Field(
        default_factory=list,
        description="最大的 N 个文件，按大小降序排列",
    )

class ImageMetadata(BaseModel):
    """图片元数据（EXIF）。"""
    width: int | None = Field(default=None, description="图片宽度（像素）")
    height: int | None = Field(default=None, description="图片高度（像素）")
    format: str | None = Field(default=None, description="图片格式，如 'JPEG'、'PNG'")
    camera_make: str | None = Field(default=None, description="相机品牌")
    camera_model: str | None = Field(default=None, description="相机型号")
    datetime_original: str | None = Field(default=None, description="拍摄时间（EXIF）")
    gps_latitude: float | None = Field(default=None, description="GPS 纬度（十进制度数）")
    gps_longitude: float | None = Field(default=None, description="GPS 经度（十进制度数）")

class DocumentMetadata(BaseModel):
    """文档元数据（PDF/Office）。"""
    title: str | None = Field(default=None, description="文档标题")
    author: str | None = Field(default=None, description="文档作者")
    page_count: int | None = Field(default=None, description="页数")
    word_count: int | None = Field(default=None, description="字数（如果可用）")
    created_time: str | None = Field(default=None, description="文档创建时间")
    modified_time: str | None = Field(default=None, description="文档修改时间")

class MediaMetadata(BaseModel):
    """音视频元数据。"""
    duration: float | None = Field(default=None, description="时长（秒）")
    width: int | None = Field(default=None, description="视频宽度（像素）")
    height: int | None = Field(default=None, description="视频高度（像素）")
    codec: str | None = Field(default=None, description="编码格式")
    bitrate: int | None = Field(default=None, description="比特率")
    sample_rate: int | None = Field(default=None, description="音频采样率")
    channels: int | None = Field(default=None, description="音频通道数")

class FileMetadataResult(BaseModel):
    """文件元数据结果。"""
    # 基础信息
    path: str = Field(description="文件路径")
    name: str = Field(description="文件名")
    extension: str = Field(description="文件扩展名（小写）")
    size: int = Field(description="文件大小（字节）")
    mime_type: str = Field(description="MIME 类型，如 'image/jpeg'、'application/pdf'")

    # 时间信息
    created: str = Field(description="创建时间，格式 YYYY-MM-DD HH:MM:SS")
    modified: str = Field(description="修改时间，格式 YYYY-MM-DD HH:MM:SS")

    # 内容 hash（可选，大文件可能耗时）
    md5: str | None = Field(default=None, description="文件内容的 MD5 哈希值（可选）")

    # 扩展元数据（按文件类型）
    image: ImageMetadata | None = Field(default=None, description="图片元数据（EXIF）")
    document: DocumentMetadata | None = Field(default=None, description="文档元数据（PDF/Office）")
    media: MediaMetadata | None = Field(default=None, description="音视频元数据")


# ============================================================
#  字段常量：用于 file_metadata_batch 的字段选择
# ============================================================

# 基础字段（所有文件都有）
BASE_FIELDS = {
    "path", "name", "extension", "size",
    "mime_type", "created", "modified", "md5",
}

# 图片字段
IMAGE_FIELDS = {
    "width", "height", "format",
    "camera_make", "camera_model", "datetime_original",
    "gps_latitude", "gps_longitude",
}

# 文档字段
DOCUMENT_FIELDS = {
    "title", "author",
    "page_count", "word_count", "created_time", "modified_time",
}

# 音视频字段
MEDIA_FIELDS = {
    "duration", "width", "height",
    "codec", "bitrate", "sample_rate", "channels",
}

# 所有字段
ALL_FIELDS = BASE_FIELDS | IMAGE_FIELDS | DOCUMENT_FIELDS | MEDIA_FIELDS

# ============================================================
#  搜索结果句柄：用于 Handle + Preview + Pagination 模式
# ============================================================

class SearchResultHandle(BaseModel):
    """搜索结果句柄。

    search_files 返回句柄 + 预览，其他工具通过 result_id 获取完整结果。
    避免上下文爆炸（不需要在上下文中传递 1000 个路径）。
    """
    result_id: str = Field(description="结果句柄，传给 copy_files/move_files/delete_files 等工具")
    total_files: int = Field(description="匹配的文件总数")
    preview: List[SearchResult] = Field(description="前 20 个文件预览")
    has_more: bool = Field(description="是否有更多结果")


class SearchResultPage(BaseModel):
    """搜索结果分页。

    用于获取 search_files 返回的句柄的分页结果。
    """
    result_id: str = Field(description="结果句柄")
    results: List[SearchResult] = Field(description="当前页的结果")
    page: int = Field(description="当前页码")
    has_more: bool = Field(description="是否有更多页")
    total_files: int = Field(description="文件总数")


# ============================================================
#  文件操作模型：用于 copy_files / move_files / delete_files
# ============================================================

class FileSearchCondition(BaseModel):
    """文件搜索条件（公共模型）。

    用于 copy_files、move_files、delete_files 等批量操作工具。
    内部调用搜索引擎搜索匹配的文件，然后执行操作。
    """
    source_dir: str = Field(description="源目录路径")
    pattern: str | None = Field(
        default=None,
        description="文件名模式，支持通配符。示例：'*.pdf'、'*.jpg,*.png'、'*'",
    )
    exclude_pattern: str | None = Field(
        default=None,
        description="排除模式，多个用逗号分隔。示例：'*.tmp,*.log'",
    )
    min_size_mb: float | None = Field(default=None, description="最小文件大小（MB）")
    max_size_mb: float | None = Field(default=None, description="最大文件大小（MB）")
    modified_before: str | None = Field(
        default=None,
        description="修改时间上限，格式 YYYY-MM-DD。示例：'2025-01-01'",
    )
    modified_after: str | None = Field(
        default=None,
        description="修改时间下限，格式 YYYY-MM-DD。示例：'2024-01-01'",
    )
    max_files: int = Field(default=1000, description="最大文件数限制")


class CopyFilesRequest(FileSearchCondition):
    """复制文件请求。"""
    target_dir: str = Field(description="目标目录路径")


class MoveFilesRequest(FileSearchCondition):
    """移动文件请求。"""
    target_dir: str = Field(description="目标目录路径")


class DeleteFilesRequest(FileSearchCondition):
    """删除文件请求。"""
    pass


class FileOperationItem(BaseModel):
    """单个文件操作结果。"""
    source: str = Field(description="源文件路径")
    target: str | None = Field(default=None, description="目标路径（delete 时为 None）")
    size: int = Field(description="文件大小（字节）")
    status: str = Field(description="状态：ok / error / skipped")
    error: str | None = Field(default=None, description="错误信息（status='error' 时）")

class FileOperationResult(BaseModel):
    """文件操作结果。"""
    total_files: int = Field(description="匹配的文件总数")
    total_size: int = Field(description="总大小（字节）")
    success_count: int = Field(description="成功操作的文件数")
    fail_count: int = Field(description="失败的文件数")
    skipped_count: int = Field(default=0, description="跳过的文件数")
    failed_files: List[FileOperationItem] = Field(
        default_factory=list,
        description="失败的文件列表")


class RenameResult(BaseModel):
    """文件重命名结果。"""
    old_path: str = Field(description="原路径")
    new_path: str = Field(description="新路径")
    success: bool = Field(description="是否成功")
    error: str | None = Field(default=None, description="错误信息（失败时）")
