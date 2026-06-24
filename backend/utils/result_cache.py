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

