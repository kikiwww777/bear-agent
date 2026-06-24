from backend.models import SearchResult
from backend.services.search_engines import FallbackSearcher


def test_fallback_timeout_does_not_wait_for_slow_engine():
    """超时后不应该等待慢引擎结束，应快速切换到下一个引擎。"""
    import time

    class SlowEngine:
        name = "slow"

        def search(self, query, max_results=500, max_depth=10):
            time.sleep(0.3)
            return []

    class FastEngine:
        name = "fast"

        def search(self, query, max_results=500, max_depth=10):
            return [
                SearchResult(
                    name="found.txt",
                    path="C:\\",
                    full_path="C:\\found.txt",
                    size=100,
                    modified="2024-01-01 00:00:00",
                )
            ]

    searcher = FallbackSearcher(
        engines=[SlowEngine(), FastEngine()],
        max_retries=1,
        timeout=0.05,
    )

    start = time.perf_counter()
    results = searcher.search("test", max_results=100)
    elapsed = time.perf_counter() - start

    assert len(results) == 1
    assert results[0].name == "found.txt"
    assert elapsed < 0.2