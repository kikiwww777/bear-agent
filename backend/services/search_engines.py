"""搜索引擎降级链。
实现两级降级：Everything SDK → os.walk 兜底。
每个引擎独立运行，按优先级依次尝试，第一个成功的返回结果。

核心设计：build_query() 返回结构化 ParsedSearch 对象，
每个引擎用自己的方式使用它（Everything 构造查询语法，WalkEngine 直接用字段）。
"""
import fnmatch
import logging
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.models import SearchResult, SearchRequest

logger = logging.getLogger(__name__)

# 每个引擎的超时时间（秒）
ENGINE_TIMEOUT = 300


# ============================================================
#  结构化查询对象
# ============================================================

@dataclass
class ParsedSearch:
    """结构化搜索参数。

    build_query() 返回此对象，每个引擎用自己的方式使用：
    - EverythingEngine：构造 Everything 查询语法（路径加引号保护特殊字符）
    - WalkEngine：直接用字段做过滤（不需要解析查询字符串）
    """
    search_path: str | None = None
    pattern: str | None = None
    extensions: list[str] = field(default_factory=list)
    min_size: int | None = None
    max_size: int | None = None
    modified_after: datetime | None = None
    modified_before: datetime | None = None


def _format_number(n: float) -> str:
    """格式化数字，整数去掉小数点。"""
    return str(int(n)) if n == int(n) else str(n)


def build_query(req: SearchRequest) -> ParsedSearch:
    """将 SearchRequest 转换为结构化 ParsedSearch 对象。

    query 字段可能包含 Everything 语法（如 "size:>0"、"ext:pdf"），
    需要解析并提取到对应的结构化字段中。

    Args:
        req: 搜索请求参数。

    Returns:
        ParsedSearch 结构化对象。
    """
    parsed = ParsedSearch()

    if req.search_path:
        parsed.search_path = req.search_path.replace("/", "\\")

    if req.extension:
        if isinstance(req.extension, list):
            parsed.extensions = [e.removeprefix(".") for e in req.extension]
        else:
            parsed.extensions = [req.extension.removeprefix(".")]

    if req.min_size_mb is not None:
        parsed.min_size = int(req.min_size_mb * 1024 * 1024)

    if req.max_size_mb is not None:
        parsed.max_size = int(req.max_size_mb * 1024 * 1024)

    if req.modified_after:
        parsed.modified_after = datetime.strptime(req.modified_after, "%Y-%m-%d")
    if req.modified_before:
        parsed.modified_before = datetime.strptime(req.modified_before, "%Y-%m-%d")

    # 解析 query 字段中的 Everything 语法
    if req.query and req.query != "*":
        remaining = []
        for part in req.query.split():
            if part.startswith("size:>"):
                parsed.min_size = _parse_size_value(part[6:])
            elif part.startswith("size:<"):
                parsed.max_size = _parse_size_value(part[6:])
            elif part.startswith("ext:"):
                exts = part[4:].split(";")
                parsed.extensions.extend(e.removeprefix(".") for e in exts)
            else:
                remaining.append(part)
        if remaining:
            parsed.pattern = " ".join(remaining)

    return parsed


def _parse_size_value(size_str: str) -> int:
    """将大小字符串转为字节数。支持：100b, 10kb, 5mb, 2gb, 1tb。"""
    units = {"tb": 1024**4, "gb": 1024**3, "mb": 1024**2, "kb": 1024, "b": 1}
    size_str = size_str.strip().lower()
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            num_part = size_str[:-len(unit)]
            if num_part:
                return int(float(num_part) * multiplier)
    return int(size_str)


# ============================================================
#  搜索引擎
# ============================================================

class SearchEngine(ABC):
    """搜索引擎抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎名称，用于日志。"""

    @abstractmethod
    def search(self, parsed: ParsedSearch, max_results: int = 500, max_depth: int | None = 10) -> list[SearchResult]:
        """执行搜索。

        Args:
            parsed: 结构化搜索参数
            max_results: 最大返回数
            max_depth: 最大扫描目录深度（仅 WalkEngine 使用）

        Returns:
            搜索结果列表，失败时返回空列表（不抛异常）
        """


class EverythingEngine(SearchEngine):
    """Everything SDK 引擎，速度最快（毫秒级）。"""

    def __init__(self):
        self._client = None

    @property
    def name(self) -> str:
        return "everything"

    def _format_query(self, parsed: ParsedSearch) -> str:
        """将 ParsedSearch 转为 Everything 查询语法字符串。

        路径用引号包裹，防止括号等特殊字符被当作查询运算符。
        """
        parts = []
        if parsed.search_path:
            parts.append(f'path:"{parsed.search_path}"')
        if parsed.extensions:
            parts.append(f'ext:{";".join(parsed.extensions)}')
        if parsed.min_size is not None:
            parts.append(f'size:>{parsed.min_size}')
        if parsed.max_size is not None:
            parts.append(f'size:<{parsed.max_size}')
        if parsed.modified_after and parsed.modified_before:
            after = parsed.modified_after.strftime("%Y-%m-%d")
            before = parsed.modified_before.strftime("%Y-%m-%d")
            parts.append(f'dm:{after}-{before}')
        elif parsed.modified_after:
            after = parsed.modified_after.strftime("%Y-%m-%d")
            parts.append(f'dm:{after}-')
        elif parsed.modified_before:
            before = parsed.modified_before.strftime("%Y-%m-%d")
            parts.append(f'dm:-{before}')
        if parsed.pattern:
            # 含特殊字符时加引号，防止 Everything 当作查询运算符
            if any(c in parsed.pattern for c in "()|!&"):
                parts.append(f'"{parsed.pattern}"')
            else:
                parts.append(parsed.pattern)
        return " ".join(parts)

    def search(self, parsed: ParsedSearch, max_results: int = 500, max_depth: int | None = 10) -> list[SearchResult]:
        try:
            if self._client is None:
                from backend.services.everything_client import EverythingClient
                self._client = EverythingClient()

            if not self._client.is_available():
                return []

            query = self._format_query(parsed)
            results = self._client.search(query, max_results=max_results)
            return results
        except FileNotFoundError:
            logger.warning("找不到 Everything64.dll，无法使用 Everything 搜索")
            return []
        except Exception as e:
            logger.warning("Everything 搜索异常: %s", e)
            return []


def _match_extensions(filename: str, extensions: list[str]) -> bool:
    """检查文件扩展名是否在允许列表中。"""
    if not extensions:
        return True
    suffix = Path(filename).suffix.lower().removeprefix(".")
    return suffix in extensions


def _match_size(size_bytes: int, min_size: int | None, max_size: int | None) -> bool:
    """检查文件大小是否在范围内。"""
    if min_size is not None and size_bytes < min_size:
        return False
    if max_size is not None and size_bytes > max_size:
        return False
    return True


def _match_modified(modified_str: str, after: datetime | None, before: datetime | None) -> bool:
    """检查修改时间是否在范围内。"""
    if not after and not before:
        return True
    try:
        dt = datetime.strptime(modified_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    if after and dt < after:
        return False
    if before and dt > before:
        return False
    return True


class WalkEngine(SearchEngine):
    """os.walk 引擎，最慢但永远可用。
    直接使用 ParsedSearch 字段做过滤，不需要解析查询字符串。
    """

    SKIP_DIRS = {
        "appdata", "application data",
        "node_modules", "__pycache__", ".venv", "venv",
        ".git", ".svn", ".hg",
        "windows", "$recycle.bin", "system volume information",
        ".cache", ".npm", ".yarn", ".pnpm",
        "caches", "temp", "tmp",
    }

    @property
    def name(self) -> str:
        return "os.walk"

    def search(self, parsed: ParsedSearch, max_results: int = 500, max_depth: int | None = 10) -> list[SearchResult]:
        search_path = parsed.search_path
        if not search_path or not Path(search_path).exists():
            return []

        results = []
        depth_limited = False
        try:
            for root, dirs, files in os.walk(search_path):
                dirs[:] = [d for d in dirs if d.lower() not in self.SKIP_DIRS]

                if max_depth is not None:
                    depth = root[len(search_path):].count(os.sep)
                    if depth > max_depth:
                        depth_limited = True
                        dirs.clear()
                        continue

                for filename in files:
                    if parsed.pattern and not fnmatch.fnmatch(filename.lower(), parsed.pattern.lower()):
                        continue
                    if not _match_extensions(filename, parsed.extensions):
                        continue

                    full_path = Path(root) / filename
                    try:
                        stat = full_path.stat()
                        if not _match_size(stat.st_size, parsed.min_size, parsed.max_size):
                            continue
                        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        if not _match_modified(modified, parsed.modified_after, parsed.modified_before):
                            continue

                        results.append(SearchResult(
                            name=filename,
                            path=root,
                            full_path=str(full_path),
                            size=stat.st_size,
                            modified=modified,
                        ))
                    except (OSError, PermissionError):
                        continue

                    if len(results) >= max_results:
                        return results
        except PermissionError:
            logger.warning("os.walk 无权限访问: %s", search_path)
        except Exception as e:
            logger.warning("os.walk 搜索异常: %s", e)

        if depth_limited:
            logger.warning(
                "已达到最大扫描深度 %d 层，更深目录未扫描。"
                "结果可能不完整，建议安装 Everything 以获取完整结果。",
                max_depth,
            )

        return results


class FallbackSearcher:
    """降级搜索器，按优先级依次尝试多个引擎。"""

    def __init__(self, engines: list[SearchEngine] | None = None, max_retries: int = 3, timeout: int = ENGINE_TIMEOUT):
        self.engines = engines or [
            EverythingEngine(),
            WalkEngine(),
        ]
        self.max_retries = max_retries
        self.timeout = timeout

    def search(self, parsed: ParsedSearch, max_results: int = 500, max_depth: int | None = 10, engine_max_results: dict[str, int] | None = None) -> list[SearchResult]:
        """按降级链搜索，第一个成功的返回结果。"""
        for engine in self.engines:
            effective_max = (engine_max_results or {}).get(engine.name, max_results)
            for attempt in range(1, self.max_retries + 1):
                logger.info("尝试搜索引擎: %s（第 %d/%d 次）", engine.name, attempt, self.max_retries)
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(engine.search, parsed, effective_max, max_depth)
                    results = future.result(timeout=self.timeout)

                    if results:
                        logger.info("搜索引擎 %s 返回 %d 条结果", engine.name, len(results))
                        return results
                    else:
                        logger.info("搜索引擎 %s 无结果，切换下一个", engine.name)
                        break

                except FutureTimeout:
                    logger.warning("搜索引擎 %s 超时 (%ss)", engine.name, self.timeout)
                except Exception as e:
                    logger.warning("搜索引擎 %s 异常: %s", engine.name, e)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

                if attempt < self.max_retries:
                    logger.info("搜索引擎 %s 重试中...", engine.name)

        logger.warning("所有搜索引擎均失败")
        return []
