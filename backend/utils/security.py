"""输入安全验证模块。

提供查询净化、路径安全校验、命令注入防护等功能。
"""
import re

def sanitize_query(query: str) -> str:
    """清洗搜索关键词，去掉可能导致问题的字符。

    处理步骤：
    1. 去掉 null 字节（\\x00）— 可能导致字符串截断
    2. 去掉控制字符（ASCII 0-31），保留 \\t \\n \\r
    3. 去掉首尾空白
    4. 空字符串返回 "*"（匹配所有）

    Args:
        query: 用户输入的搜索关键词

    Returns:
        清洗后的安全查询字符串
    """
    # 去掉 null 字节
    query = query.replace("\x00", "")
    # 去掉控制字符，保留 tab(9)、换行(10)、回车(13)
    query = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', query)
    # 去掉首尾空白
    query = query.strip()
    # 空字符串返回通配符
    if not query:
        return "*"
    return query

def validate_path(path: str | None) -> str | None:
    """验证路径安全性，防止路径穿越。

    Args:
        path: 用户指定的搜索路径

    Returns:
        净化后的路径（正斜杠统一转反斜杠）

    Raises:
        ValueError: 路径包含 ".." 时抛出
    """
    if path is None:
        return None
    if ".." in path:
        raise ValueError(f"路径不允许包含 '..': {path}")
    # Everything 的 path: 语法必须用反斜杠
    path = path.replace("/", "\\")
    # 归一化：连续反斜杠合并为单个（LLM 可能传 C:\\）
    while "\\\\" in path:
        path = path.replace("\\\\", "\\")
    return path

