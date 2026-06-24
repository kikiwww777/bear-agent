"""通用格式化工具函数。"""
def format_size(size_bytes: int) -> str:
    """将字节数转为人类可读的大小字符串。

    Args:
        size_bytes: 文件大小（字节）。

    Returns:
        如 "7.0 MB"、"512 KB"、"256 bytes"。
    """
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024 ** 3:.1f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} bytes"
