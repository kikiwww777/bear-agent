"""文件类型分类映射。

参照 Windows 存储感知的分类方式，支持两级判定：
1. 路径优先：Program Files → 安装的应用，Windows → 系统和保留
2. 扩展名兜底：jpg → 图片，mp4 → 视频

供 file_stats 等工具使用，帮助 LLM 和用户快速理解文件构成。
"""
from pathlib import PureWindowsPath

# ============================================================
#  路径分类（优先级高于扩展名）
# ============================================================

# 安装的应用：程序安装目录和用户应用数据
_APP_PATHS = {
    "program files",
    "program files (x86)",
    "windowsapps",                  # UWP 应用
}

# 系统和保留：Windows 系统目录
_SYSTEM_PATHS = {
    "windows",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "boot",
    "efi",
}

# 临时文件：临时目录（路径判定，和扩展名判定互补）
_TEMP_PATHS = {
    "temp",
    "tmp",
    "cache",
    "caches",
}

# AppData 下的子目录归属
# AppData\Local\Programs → 安装的应用
# AppData\Local\Temp → 临时文件
# AppData\Local 和 AppData\Roaming 下其他 → 安装的应用（应用数据）
_APPDATA_SUBCATEGORY = {
    "temp": "临时文件",
    "tmp": "临时文件",
    "cache": "临时文件",
    "caches": "临时文件",
    "crashdumps": "临时文件",
    "logs": "临时文件",
}


def _classify_by_path(full_path: str) -> str | None:
    """按路径判定文件分类。

    返回分类名，无法判定返回 None（交给扩展名兜底）。
    """
    # 统一用小写、正斜杠比较
    path = full_path.replace("\\", "/").lower()
    parts = PureWindowsPath(full_path).parts
    parts_lower = [p.lower() for p in parts]

    # Program Files → 安装的应用
    for p in parts_lower:
        if p in _APP_PATHS:
            return "安装的应用"

    # Windows / 系统目录 → 系统和保留
    for p in parts_lower:
        if p in _SYSTEM_PATHS:
            return "系统和保留"

    # AppData 子目录判定
    if "appdata" in parts_lower:
        idx = parts_lower.index("appdata")
        # AppData 下面的子目录（Local / Roaming / LocalLow）
        if idx + 2 < len(parts_lower):
            subdir = parts_lower[idx + 2]  # AppData/Local/<subdir>
            if subdir in _APPDATA_SUBCATEGORY:
                return _APPDATA_SUBCATEGORY[subdir]
        # AppData 下的其他目录 → 安装的应用（应用数据）
        return "安装的应用"

    # 临时目录
    for p in parts_lower:
        if p in _TEMP_PATHS:
            return "临时文件"

    return None


# ============================================================
#  扩展名分类（兜底）
# ============================================================

# 扩展名 → 分类映射（小写，不含点号）
FILE_CATEGORIES: dict[str, set[str]] = {
    "图片": {
        "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico",
        "tiff", "tif", "raw", "heic", "heif", "psd", "ai", "eps",
    },
    "视频": {
        "mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "m4v",
        "3gp", "ts", "mpeg", "mpg", "vob", "mts", "m2ts",
    },
    "音乐": {
        "mp3", "wav", "flac", "aac", "ogg", "wma", "m4a", "opus",
        "aiff", "ape", "mid", "midi",
    },
    "文档": {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
        "txt", "rtf", "odt", "ods", "odp", "csv", "md", "epub",
        "pages", "numbers", "key", "one", "pub", "xps",
    },
    "压缩包": {
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso",
        "cab", "zst", "lz4", "tgz", "txz",
    },
    "代码": {
        "py", "js", "ts", "jsx", "tsx", "java", "c", "cpp", "h",
        "hpp", "cs", "go", "rs", "rb", "php", "swift", "kt",
        "html", "css", "scss", "less", "json", "xml", "yaml", "yml",
        "toml", "ini", "cfg", "conf", "sql", "sh", "bat", "ps1",
        "vue", "svelte", "dart", "lua", "r", "m", "mm",
    },
    "安装包": {
        "exe", "msi", "msix", "appx", "apk", "dmg", "deb", "rpm",
        "appimage", "snap", "flatpak",
    },
    "临时文件": {
        "tmp", "temp", "log", "bak", "old", "swp", "cache",
        "crdownload", "part", "partial",
    },
    "字体": {
        "ttf", "otf", "woff", "woff2", "eot",
    },
    "磁盘镜像": {
        "img", "vmdk", "vhd", "vhdx", "qcow2", "vdi",
    },
}

# 反向索引：扩展名 → 分类名（运行时构建，加速查询）
_EXT_TO_CATEGORY: dict[str, str] = {}
for _cat, _exts in FILE_CATEGORIES.items():
    for _ext in _exts:
        _EXT_TO_CATEGORY[_ext] = _cat


def classify_extension(ext: str) -> str:
    """将扩展名归入文件分类（仅扩展名，不考虑路径）。

    Args:
        ext: 文件扩展名（不含点号），如 "jpg"、"pdf"。大小写不敏感。

    Returns:
        分类名（如 "图片"、"文档"），未命中返回 "其他"。
    """
    return _EXT_TO_CATEGORY.get(ext.lower().lstrip("."), "其他")


def classify_file(full_path: str, ext: str) -> str:
    """对文件进行分类：路径优先，扩展名兜底。

    判定顺序：
    1. 路径命中 → 返回路径分类（安装的应用/系统和保留/临时文件）
    2. 路径未命中 → 返回扩展名分类（图片/视频/文档/...）
    3. 都未命中 → 返回 "其他"

    Args:
        full_path: 文件完整路径，如 "C:\\Program Files\\App\\app.dll"
        ext: 文件扩展名（不含点号），如 "dll"、"jpg"。大小写不敏感。

    Returns:
        分类名。
    """
    path_result = _classify_by_path(full_path)
    if path_result is not None:
        return path_result
    return classify_extension(ext)
