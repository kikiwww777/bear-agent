"""通用文件路径查找工具。
在多个可能的位置中自动查找目标文件，支持环境变量、候选路径、注册表、系统目录递归搜索。
用于自动定位第三方 SDK/工具的 DLL 和可执行文件。
"""
import os
import winreg
from pathlib import Path

def find_file(
    filename: str,
    env_var: str | None = None,
    search_paths: list[Path] | None = None,
    search_subdirs: list[str] | None = None,
) -> Path | None:
    """在多个可能的位置中查找目标文件，返回完整路径。

    搜索优先级：
    1. 环境变量指定的路径
    2. 调用方提供的候选路径
    3. 系统默认安装目录

    Args:
        filename: 要查找的文件名，如 "Everything64.dll"
        env_var: 可选的环境变量名，用户可通过环境变量显式指定路径
        search_paths: 调用方提供的候选目录列表
        search_subdirs: 在每个候选目录下要额外检查的子目录名，如 ["dll", "bin"]
    """
    # Step 1: 环境变量
    if env_var:
        env_path = os.environ.get(env_var)
        if env_path:
            p = Path(env_path)
            # 用户可能直接指向文件本身
            if p.is_file() and p.name == filename:
                return p
            # 用户指向目录，在目录下找
            candidate = p / filename
            if candidate.is_file():
                return candidate
            if p.is_dir():
                # 递归搜索
                found = _search_recursive(p, filename)
                if found:
                    return found

    # Step 2: 候选路径
    if search_paths:
        for base in search_paths:
            if not base.exists():
                continue
            # 直接检查 base/filename
            candidate = base / filename
            if candidate.is_file():
                return candidate
            # 检查 base/subdir/filename
            if search_subdirs:
                for subdir in search_subdirs:
                    candidate = base / subdir / filename
                    if candidate.is_file():
                        return candidate

    # Step 3: 系统默认路径递归搜索
    for base in _get_system_search_paths():
        if not base.exists():
            continue
        found = _search_recursive(base, filename, max_depth=3)
        if found:
            return found

    return None

def _get_system_search_paths() -> list[Path]:
    """返回系统常见的软件安装目录。"""
    paths = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path("D:/Program Files"),
        Path("D:/Program Files (x86)"),
        Path.home() / "AppData" / "Local",
    ]

    # 从注册表读取 Everything 安装路径，加到列表里
    everything_path = _get_everything_install_path()
    if everything_path:
        paths.insert(0,everything_path)

    return [p for p in paths if p.exists()]


def _search_recursive(base: Path, filename: str, max_depth: int = 3) -> Path | None:
    """在目录下递归查找文件，限制深度避免扫描太慢。"""
    if max_depth < 0:
        return None
    try:
        for entry in base.iterdir():
            if entry.is_file() and entry.name == filename:
                return entry
            if entry.is_dir():
                found = _search_recursive(entry, filename, max_depth - 1)
                if found:
                    return found
    except PermissionError:
        pass
    return None


def _get_everything_install_path() -> Path | None:
    """从注册表读取 Everything 安装路径。"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Everything"
        )
        install_path, _ = winreg.QueryValueEx(key, "InstallLocation")
        winreg.CloseKey(key)
        return Path(install_path) if install_path else None
    except (FileNotFoundError, OSError):
        return None
