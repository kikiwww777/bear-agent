import time
import uuid
from collections import OrderedDict
from threading import Lock

CACHE_TTL = 3600
MAX_CACHE_ENTRIES = 100
PREVIEW_SIZE = 20

class ResultCache:
    def __init__(self, ttl: int = CACHE_TTL, max_entries: int = MAX_CACHE_ENTRIES):
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._ttl = ttl
        self._max_entries = max_entries
        self._lock = Lock()

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired_keys = [
            key for key, value in self._cache.items()
            if now - value["last_access"] > self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]

    def _evict_lru(self) -> None:
        while len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)

    def generate_id(self, prefix: str = "search") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def put(self, result_id: str, results: list) -> None:
        with self._lock:
            self._cleanup_expired()
            self._evict_lru()
            self._cache[result_id] = {
                "results": results,
                "last_access": time.time(),
            }
            self._cache.move_to_end(result_id)

    def get(self, result_id: str) -> list | None:
        with self._lock:
            if result_id not in self._cache:
                return None
            entry = self._cache[result_id]
            now = time.time()
            if now - entry["last_access"] > self._ttl:
                del self._cache[result_id]
                return None
            entry["last_access"] = now
            self._cache.move_to_end(result_id)
            return entry["results"]

    def remove(self, result_id: str) -> bool:
        with self._lock:
            if result_id in self._cache:
                del self._cache[result_id]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

_cache = ResultCache()
def get_result_cache() -> ResultCache:
    return _cache


# ──────────────────────────────────────────────
#  缓存数据提取 helper
# ──────────────────────────────────────────────

def extract_files_from_results(results: list) -> list:
    """将缓存结果归一化为扁平的文件列表。

    缓存中可能存储两种数据：
    - list[SearchResult]（来自 search_files）：直接返回
    - list[DuplicateResult]（来自 find_duplicates）：展开所有组的 .files

    判断方式：检查首个元素的 .files 属性（DuplicateResult 特有）。

    Args:
        results: 从 ResultCache 读取的原始列表

    Returns:
        扁平的 SearchResult 列表。空输入返回空列表。
    """
    if not results:
        return []

    first = results[0]
    # DuplicateResult 有 .files 属性（list[SearchResult]），展开所有组
    if hasattr(first, 'files'):
        flat: list = []
        for item in results:
            flat.extend(item.files)
        return flat

    # SearchResult 或其他类型 — 原样返回
    return results

