"""路径权限管理模块。

管理用户配置的路径访问权限，支持三种权限级别：
- READ_ONLY：只读，允许查看但不允许修改/删除
- BLOCKED：完全禁止访问
- ALLOW：允许所有操作（覆盖默认的只读限制）

权限存储在项目根目录的 path_permissions.json 文件中，内存缓存加速查询。

使用方式：
    from backend.utils.path_permissions import get_permission, save_permission

    # 查询权限
    perm = get_permission("C:\\Windows")  # → "READ_ONLY"

    # 设置权限
    save_permission("D:\\重要项目", "READ_ONLY")

    # LLM 通过 set_path_permission 工具调用 save_permission

设计决策：
    - 默认权限：系统目录只读，用户目录可读写
    - 最长前缀匹配：子目录权限可覆盖父目录权限
    - 配置文件 + 内存缓存：文件持久化，缓存加速查询
    - 路径统一小写：Windows 路径不区分大小写
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("bear_agent.path_permissions")


# ──────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────

# 权限类型
PERMISSIONS = ("READ_ONLY", "BLOCKED", "ALLOW")

# 配置文件路径（项目根目录下）
_CONFIG_FILE: Path = Path(__file__).parent.parent.parent / "path_permissions.json"

# 内存缓存：{归一化路径: 权限}
_cache: dict[str, str] = {}

# 默认权限：系统目录只读
_DEFAULT_PERMISSIONS: dict[str, str] = {
    "C:\\Windows": "READ_ONLY",
    "C:\\Program Files": "READ_ONLY",
    "C:\\Program Files (x86)": "READ_ONLY",
    "C:\\$Recycle.Bin": "READ_ONLY",
}


# ──────────────────────────────────────────────
#  路径归一化（内部用）
# ──────────────────────────────────────────────

def _normalize(path: str) -> str:
    """归一化路径，用于存储和比较。

    处理：
    - 正斜杠 → 反斜杠
    - 合并连续反斜杠
    - 去掉末尾反斜杠（C:\\Users\\ → C:\\Users）
    - 统一小写（Windows 路径不区分大小写）

    Args:
        path: 原始路径

    Returns:
        归一化后的路径（小写）
    """
    path = path.replace("/", "\\")
    while "\\\\" in path:
        path = path.replace("\\\\", "\\")
    # 去掉末尾反斜杠（保留盘符根目录的反斜杠，如 C:\）
    if path.endswith("\\") and len(path) > 3:
        path = path.rstrip("\\")
    return path.lower()


# ──────────────────────────────────────────────
#  加载权限
# ──────────────────────────────────────────────

def load_permissions() -> dict[str, str]:
    """加载权限：默认权限 + 用户配置（用户配置覆盖默认）。

    加载顺序：
    1. 加载 _DEFAULT_PERMISSIONS（系统目录只读）
    2. 从 path_permissions.json 加载用户配置（覆盖默认）

    Returns:
        归一化后的权限字典 {normalized_path: permission}
    """
    global _cache

    # 从默认权限开始
    result: dict[str, str] = {}
    for path, perm in _DEFAULT_PERMISSIONS.items():
        result[_normalize(path)] = perm

    # 从配置文件加载（覆盖默认）
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                user_permissions: dict[str, str] = json.load(f)
            for path, perm in user_permissions.items():
                if perm in PERMISSIONS:
                    result[_normalize(path)] = perm
                    logger.debug("加载用户权限：%s -> %s", path, perm)
                else:
                    logger.warning("无效权限 '%s'，路径: %s，跳过", perm, path)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("加载权限配置失败: %s", e)

    _cache = result
    logger.info("已加载 %d 条路径权限", len(_cache))
    return result


# ──────────────────────────────────────────────
#  保存权限
# ──────────────────────────────────────────────

def save_permission(path: str, permission: str) -> None:
    """保存一条权限到配置文件和内存缓存。

    Args:
        path: 目标路径（保留原始大小写，方便用户阅读配置文件）
        permission: READ_ONLY / BLOCKED / ALLOW

    Raises:
        ValueError: permission 不在 PERMISSIONS 中
        OSError: 配置文件写入失败
    """
    if permission not in PERMISSIONS:
        raise ValueError(f"无效权限 '{permission}'，可选: {PERMISSIONS}")

    normalized = _normalize(path)

    # 读取现有配置
    user_permissions: dict[str, str] = {}
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                user_permissions = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("读取现有配置失败，将创建新配置文件")

    # 更新配置
    user_permissions[path] = permission

    # 写回文件
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(user_permissions, f, ensure_ascii=False, indent=2)
        logger.info("已保存路径权限: %s = %s", path, permission)
    except OSError as e:
        logger.error("保存权限配置失败: %s", e)
        raise

    # 更新缓存
    _cache[normalized] = permission


# ──────────────────────────────────────────────
#  查询权限（核心逻辑）
# ──────────────────────────────────────────────

def get_permission(path: str) -> str:
    """查询路径的权限，返回最匹配的权限。

    匹配逻辑：找权限表中最长的前缀匹配。

    示例：
        权限表有 "D:\\项目" → "READ_ONLY"
        查询 "D:\\项目\\子目录\\文件.txt" → 匹配到 "D:\\项目" → 返回 "READ_ONLY"
        查询 "D:\\其他" → 没有匹配 → 返回 "ALLOW"（默认）

        权限表有 "D:\\项目" → "READ_ONLY"
        权限表有 "D:\\项目\\公开" → "ALLOW"
        查询 "D:\\项目\\公开\\文件.txt" → 匹配到 "D:\\项目\\公开"（更长）→ 返回 "ALLOW"

    Args:
        path: 要查询的路径

    Returns:
        权限字符串：READ_ONLY / BLOCKED / ALLOW
    """
    if not _cache:
        load_permissions()

    normalized = _normalize(path)

    # 找最长前缀匹配
    best_match = ""
    best_permission = "ALLOW"  # 默认允许

    for cached_path, permission in _cache.items():
        # 精确匹配
        if normalized == cached_path:
            return permission
        # 前缀匹配：cached_path 是 normalized 的前缀
        if normalized.startswith(cached_path + "\\"):
            if len(cached_path) > len(best_match):
                best_match = cached_path
                best_permission = permission

    return best_permission


# ──────────────────────────────────────────────
#  模块初始化
# ──────────────────────────────────────────────

# 模块导入时自动加载
load_permissions()
