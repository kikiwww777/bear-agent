"""路径权限设置工具。

允许用户通过对话设置路径访问权限，控制哪些目录可以操作、哪些只能查看。
LLM 调用此工具来响应用户的权限配置请求。

典型场景：
- "D:\重要项目 只能看不能删" → set_path_permission(path="D:\重要项目", permission="READ_ONLY")
- "C:\Windows 可以访问" → set_path_permission(path="C:\Windows", permission="ALLOW")
- "私人文件夹禁止访问" → set_path_permission(path="...", permission="BLOCKED")

权限说明：
- READ_ONLY：只读，可以查看（file_stats、search_files）但不能删除/修改
- BLOCKED：完全禁止访问，连查看都不允许
- ALLOW：允许所有操作（覆盖默认的系统目录只读限制）

权限存储在 path_permissions.json 中，重启后仍然生效。
"""
from langchain_core.tools import tool

from backend.utils.path_permissions import save_permission, PERMISSIONS


@tool("set_path_permission",description="设置路径访问权限工具")
def set_path_permission(path: str, permission: str) -> str:
    """设置路径访问权限。

    Args:
        path: 目标路径（如 "D:\\重要项目"）
        permission: 权限级别
            - READ_ONLY：只读，可以查看但不能删除/修改
            - BLOCKED：完全禁止访问
            - ALLOW：允许所有操作

    Returns:
        设置结果消息（中文，给 LLM 和用户看）
    """
    if not permission:
        return f"缺少 permission 参数，可选: {PERMISSIONS}"

    if permission not in PERMISSIONS:
        return f"无效权限 '{permission}'，可选: {PERMISSIONS}"

    try:
        save_permission(path, permission)
        return f"已设置路径权限：{path} = {permission}"
    except OSError as e:
        return f"保存权限失败：{e}"
