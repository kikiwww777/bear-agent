"""文件元数据读取工具。

读取文件的基础信息、MIME 类型、时间戳，以及图片 EXIF、文档属性、音视频信息等扩展元数据。

工具设计（统一入口，两种模式）：
- file_metadata(path="...") → 单文件，返回 FileMetadataResult
- file_metadata(paths=[...], fields=[...]) → 批量 + 字段过滤，返回 list[dict]

设计决策：
- 基础层保证 100% 覆盖（所有文件都能返回大小、时间、MIME 类型）
- 扩展层按文件类型解析（图片 EXIF、PDF 属性、音视频信息）
- 解析失败返回 None，不抛异常（优雅降级）
- 懒加载依赖（只在需要时导入 Pillow、PyPDF2 等）
- 字段过滤用 Pydantic 的 model_dump(include=...)，不手动构造 dict
"""
import logging
import mimetypes
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

from backend.models import (
    FileMetadataResult, ImageMetadata, DocumentMetadata, MediaMetadata,
    BASE_FIELDS, IMAGE_FIELDS, DOCUMENT_FIELDS, MEDIA_FIELDS,
    ALL_FIELDS,
)
from backend.tools.find_duplicates import _compute_md5

logger = logging.getLogger(__name__)

# ============================================================
#  MIME 类型常量：用于扩展元数据分发
# ============================================================

_OFFICE_MIME_TYPES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# ============================================================
#  基础层
# ============================================================

def _get_mime_type(file_path: Path) -> str:
    """获取文件的 MIME 类型。"""
    mime_type = mimetypes.guess_type(str(file_path))[0]
    return mime_type or "application/octet-stream"


def _get_file_times(file_path: Path) -> tuple[str, str]:
    """获取文件的创建、修改时间。"""
    stat = file_path.stat()
    created = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return created, modified


# ============================================================
#  GPS 坐标解析
# ============================================================

def _gps_to_decimal(gps_coord: tuple) -> float:
    """将 EXIF GPS 坐标（度/分/秒）转为十进制度数。

    EXIF GPS 格式：((deg_num, deg_den), (min_num, min_den), (sec_num, sec_den))
    转换公式：decimal = deg + min/60 + sec/3600
    """
    d = gps_coord[0][0] / gps_coord[0][1]
    m = gps_coord[1][0] / gps_coord[1][1]
    s = gps_coord[2][0] / gps_coord[2][1]
    return d + m / 60 + s / 3600


# ============================================================
#  扩展层
# ============================================================

def _get_image_metadata(file_path: Path) -> ImageMetadata | None:
    """读取图片 EXIF 元数据。"""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS

        with Image.open(file_path) as img:
            width, height = img.size
            image_meta = ImageMetadata(width=width, height=height, format=img.format)

            exif_data = img.getexif()
            if exif_data:
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    if tag_name == "Make":
                        image_meta.camera_make = str(value)
                    elif tag_name == "Model":
                        image_meta.camera_model = str(value)
                    elif tag_name == "DateTimeOriginal":
                        image_meta.datetime_original = str(value)

                # GPS 信息
                gps_info = exif_data.get_ifd(0x8825)
                if gps_info:
                    gps_data = {
                        GPSTAGS.get(tag, tag): value
                        for tag, value in gps_info.items()
                    }
                    if "GPSLatitude" in gps_data and "GPSLongitude" in gps_data:
                        image_meta.gps_latitude = _gps_to_decimal(gps_data["GPSLatitude"])
                        image_meta.gps_longitude = _gps_to_decimal(gps_data["GPSLongitude"])

            return image_meta
    except Exception as e:
        logger.debug("图片元数据读取失败: %s — %s", file_path, e)
        return None


def _get_pdf_metadata(file_path: Path) -> DocumentMetadata | None:
    """读取 PDF 文档元数据。"""
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(file_path))
        info = reader.metadata

        doc_meta = DocumentMetadata(
            title=getattr(info, "title", None),
            author=getattr(info, "author", None),
            page_count=len(reader.pages),
        )

        if hasattr(info, "creation_date") and info.creation_date:
            doc_meta.created_time = str(info.creation_date)
        if hasattr(info, "modification_date") and info.modification_date:
            doc_meta.modified_time = str(info.modification_date)

        return doc_meta
    except Exception as e:
        logger.debug("PDF 元数据读取失败: %s — %s", file_path, e)
        return None


def _get_document_metadata(file_path: Path) -> DocumentMetadata | None:
    """读取 Office 文档元数据（Word/Excel）。"""
    try:
        from docx import Document

        doc = Document(str(file_path))
        props = doc.core_properties

        return DocumentMetadata(
            title=props.title,
            author=props.author,
            created_time=str(props.created) if props.created else None,
            modified_time=str(props.modified) if props.modified else None,
        )
    except Exception as e:
        logger.debug("Office 文档元数据读取失败: %s — %s", file_path, e)
        return None


def _get_media_metadata(file_path: Path) -> MediaMetadata | None:
    """读取音视频元数据。"""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(file_path))
        if audio is None:
            return None

        media_meta = MediaMetadata()

        if hasattr(audio.info, "length") and audio.info.length:
            media_meta.duration = audio.info.length
        if hasattr(audio.info, "width") and audio.info.width:
            media_meta.width = audio.info.width
        if hasattr(audio.info, "height") and audio.info.height:
            media_meta.height = audio.info.height
        if hasattr(audio.info, "codec") and audio.info.codec:
            media_meta.codec = audio.info.codec
        if hasattr(audio.info, "bitrate") and audio.info.bitrate:
            media_meta.bitrate = audio.info.bitrate
        if hasattr(audio.info, "sample_rate") and audio.info.sample_rate:
            media_meta.sample_rate = audio.info.sample_rate
        if hasattr(audio.info, "channels") and audio.info.channels:
            media_meta.channels = audio.info.channels

        return media_meta
    except Exception as e:
        logger.debug("音视频元数据读取失败: %s — %s", file_path, e)
        return None


# ============================================================
#  核心实现
# ============================================================

def _file_metadata_impl(path: str, include_md5: bool = False) -> FileMetadataResult | None:
    """读取文件的元数据信息。

    Args:
        path: 文件完整路径。
        include_md5: 是否计算 MD5 哈希值。

    Returns:
        FileMetadataResult，失败返回 None。
    """
    file_path = Path(path)

    if not file_path.exists():
        logger.warning("文件不存在: %s", path)
        return None
    if not file_path.is_file():
        logger.warning("不是文件: %s", path)
        return None

    # 基础层
    stat = file_path.stat()
    extension = file_path.suffix.lower().lstrip(".")
    mime_type = _get_mime_type(file_path)
    created, modified = _get_file_times(file_path)
    md5 = _compute_md5(file_path) if include_md5 else None

    # 扩展层
    image_meta = None
    document_meta = None
    media_meta = None

    if mime_type.startswith("image/"):
        image_meta = _get_image_metadata(file_path)
    elif mime_type == "application/pdf":
        document_meta = _get_pdf_metadata(file_path)
    elif mime_type in _OFFICE_MIME_TYPES:
        document_meta = _get_document_metadata(file_path)
    elif mime_type.startswith("audio/") or mime_type.startswith("video/"):
        media_meta = _get_media_metadata(file_path)

    return FileMetadataResult(
        path=str(file_path),
        name=file_path.name,
        extension=extension,
        size=stat.st_size,
        mime_type=mime_type,
        created=created,
        modified=modified,
        md5=md5,
        image=image_meta,
        document=document_meta,
        media=media_meta,
    )


def _filter_fields(result: FileMetadataResult, fields: list[str] | None) -> dict:
    """用 Pydantic 的 model_dump 做字段过滤。"""
    if fields is None:
        return result.model_dump()

    # 分离基础字段和嵌套字段
    base_include = {f for f in fields if f in BASE_FIELDS}
    image_include = {f for f in fields if f in IMAGE_FIELDS}
    doc_include = {f for f in fields if f in DOCUMENT_FIELDS}
    media_include = {f for f in fields if f in MEDIA_FIELDS}

    filtered = {}

    # 基础字段
    for f in base_include:
        filtered[f] = getattr(result, f, None)

    # 嵌套字段
    if result.image and image_include:
        filtered["image"] = result.image.model_dump(include=image_include)
    if result.document and doc_include:
        filtered["document"] = result.document.model_dump(include=doc_include)
    if result.media and media_include:
        filtered["media"] = result.media.model_dump(include=media_include)

    return filtered


# ============================================================
#  工具入口
# ============================================================

@tool("file_metadata",description="读取文件元数据工具")
def file_metadata(
    path: str | None = None,
    paths: list[str] | None = None,
    fields: list[str] | None = None,
    include_md5: bool = False,
) -> FileMetadataResult | list[dict] | None:
    """读取文件的元数据信息，支持单文件和批量查询两种模式。

    模式 1 — 单文件：传 path 参数，返回 FileMetadataResult（完整元数据）。
    模式 2 — 批量查询：传 paths 和 fields 参数，返回 list[dict]（指定字段）。

    基础层（所有文件）：大小、时间、MIME 类型、MD5（可选）
    扩展层（按类型）：图片 EXIF、PDF 属性、Office 属性、音视频信息

    对于无法解析的文件类型，扩展元数据返回 None，不报错。

    Args:
        path: 文件完整路径（模式 1）。
        paths: 文件路径列表（模式 2）。
        fields: 需要的字段列表（模式 2），None 返回全部字段。
            基础字段：path, name, extension, size, mime_type, created, modified, md5
            图片字段：width, height, format, camera_make, camera_model, datetime_original, gps_latitude, gps_longitude
            文档字段：title, author, page_count, word_count, created_time, modified_time
            音视频字段：duration, width, height, codec, bitrate, sample_rate, channels
        include_md5: 是否计算 MD5 哈希值（大文件可能耗时）。

    Returns:
        模式 1: FileMetadataResult，包含文件的各种元数据。文件不存在返回 None。
        模式 2: list[dict]，每个字典包含指定的字段。查询失败的文件包含 error 字段。
    """
    # ── 模式 2：批量查询 ──
    if paths:
        results = []
        for p in paths:
            try:
                result = _file_metadata_impl(p, include_md5=include_md5)
                if result is None:
                    results.append({"path": p, "error": "文件不存在或不是文件"})
                else:
                    results.append(_filter_fields(result, fields))
            except Exception as e:
                logger.error("批量查询失败: %s — %s", p, e)
                results.append({"path": p, "error": str(e)})
        return results

    # ── 模式 1：单文件 ──
    if path:
        return _file_metadata_impl(path, include_md5=include_md5)

    return None
