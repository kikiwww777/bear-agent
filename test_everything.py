"""搜索模块测试。

覆盖：查询构建、搜索引擎、降级链、工具层。
"""
import os
import tempfile
from datetime import datetime
from pathlib import Path

from backend.models import SearchRequest, SearchResult
from backend.services.search_engines import (
    build_query,
    ParsedSearch,
    _match_extensions,
    _match_size,
    _match_modified,
    EverythingEngine,
    WalkEngine,
    FallbackSearcher,
)


# ============================================================
#  build_query: SearchRequest → ParsedSearch
# ============================================================

def test_build_query_keyword():
    """纯关键词。"""
    req = SearchRequest(query="报告")
    parsed = build_query(req)
    assert parsed.pattern == "报告"
    assert parsed.search_path is None


def test_build_query_wildcard():
    """通配符 * 不设 pattern。"""
    req = SearchRequest(query="*")
    parsed = build_query(req)
    assert parsed.pattern is None


def test_build_query_path():
    """限定路径。"""
    req = SearchRequest(query="*.pdf", search_path="C:\\Users\\Docs")
    parsed = build_query(req)
    assert parsed.search_path == "C:\\Users\\Docs"
    assert parsed.pattern == "*.pdf"


def test_build_query_path_slash():
    """路径中的正斜杠转反斜杠。"""
    req = SearchRequest(query="*.pdf", search_path="C:/Users/Docs")
    parsed = build_query(req)
    assert parsed.search_path == "C:\\Users\\Docs"


def test_build_query_extension_single():
    """单个扩展名。"""
    req = SearchRequest(query="*", extension=".pdf")
    parsed = build_query(req)
    assert parsed.extensions == ["pdf"]


def test_build_query_extension_multiple():
    """多个扩展名。"""
    req = SearchRequest(query="*", extension=[".jpg", ".png"])
    parsed = build_query(req)
    assert parsed.extensions == ["jpg", "png"]


def test_build_query_size():
    """大小范围。"""
    req = SearchRequest(query="*", min_size_mb=10, max_size_mb=100)
    parsed = build_query(req)
    assert parsed.min_size == 10 * 1024 * 1024
    assert parsed.max_size == 100 * 1024 * 1024


def test_build_query_date_range():
    """时间范围。"""
    req = SearchRequest(query="*", modified_after="2024-01-01", modified_before="2024-12-31")
    parsed = build_query(req)
    assert parsed.modified_after == datetime(2024, 1, 1)
    assert parsed.modified_before == datetime(2024, 12, 31)


def test_build_query_full():
    """全条件组合。"""
    req = SearchRequest(
        query="报告",
        search_path="C:\\Users",
        extension=[".pdf", ".docx"],
        min_size_mb=1,
        max_size_mb=50,
        modified_after="2024-01-01",
        modified_before="2024-12-31",
    )
    parsed = build_query(req)
    assert parsed.search_path == "C:\\Users"
    assert parsed.extensions == ["pdf", "docx"]
    assert parsed.min_size == 1024 * 1024
    assert parsed.max_size == 50 * 1024 * 1024
    assert parsed.modified_after is not None
    assert parsed.modified_before is not None
    assert parsed.pattern == "报告"


# ============================================================
#  辅助函数
# ============================================================

def test_match_extensions():
    """扩展名匹配。"""
    assert _match_extensions("test.pdf", ["pdf"]) is True
    assert _match_extensions("test.PDF", ["pdf"]) is True
    assert _match_extensions("test.pdf", ["jpg"]) is False
    assert _match_extensions("test.pdf", None) is True
    assert _match_extensions("test.pdf", []) is True


def test_match_size():
    """大小匹配。"""
    assert _match_size(100, None, None) is True
    assert _match_size(100, 50, None) is True
    assert _match_size(100, 200, None) is False
    assert _match_size(100, None, 200) is True
    assert _match_size(100, None, 50) is False
    assert _match_size(100, 50, 200) is True


def test_match_modified():
    """时间匹配。"""
    from datetime import datetime
    assert _match_modified("2024-06-15 12:00:00", None, None) is True
    assert _match_modified("2024-06-15 12:00:00", datetime(2024, 1, 1), None) is True
    assert _match_modified("2024-06-15 12:00:00", datetime(2025, 1, 1), None) is False
    assert _match_modified("2024-06-15 12:00:00", None, datetime(2024, 12, 31)) is True
    assert _match_modified("2024-06-15 12:00:00", None, datetime(2024, 1, 1)) is False


# ============================================================
#  WalkEngine: 用临时目录测试真实文件搜索
# ============================================================

def _walk_parsed(tmpdir, pattern=None, extensions=None, min_size=None, max_size=None, max_depth=5, max_results=100):
    """辅助函数：构造 ParsedSearch 并调用 WalkEngine。"""
    parsed = ParsedSearch(search_path=tmpdir, pattern=pattern, extensions=extensions or [], min_size=min_size, max_size=max_size)
    engine = WalkEngine()
    return engine.search(parsed, max_results=max_results, max_depth=max_depth)


def test_walk_engine_basic():
    """WalkEngine 基本搜索。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "a.pdf").write_text("pdf")
        (Path(tmpdir) / "b.txt").write_text("txt")
        (Path(tmpdir) / "c.jpg").write_text("jpg")

        results = _walk_parsed(tmpdir)
        names = {r.name for r in results}
        assert "a.pdf" in names
        assert "b.txt" in names
        assert "c.jpg" in names


def test_walk_engine_extension_filter():
    """WalkEngine 扩展名过滤。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "a.pdf").write_text("pdf")
        (Path(tmpdir) / "b.txt").write_text("txt")

        results = _walk_parsed(tmpdir, extensions=["pdf"])
        names = {r.name for r in results}
        assert "a.pdf" in names
        assert "b.txt" not in names


def test_walk_engine_size_filter():
    """WalkEngine 大小过滤。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "big.bin").write_bytes(b"x" * 2000)
        (Path(tmpdir) / "small.bin").write_bytes(b"x" * 10)

        results = _walk_parsed(tmpdir, min_size=1024)
        names = {r.name for r in results}
        assert "big.bin" in names
        assert "small.bin" not in names


def test_walk_engine_subdir():
    """WalkEngine 子目录搜索。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        sub = Path(tmpdir) / "sub1" / "sub2"
        sub.mkdir(parents=True)
        (sub / "deep.txt").write_text("deep")

        results = _walk_parsed(tmpdir, pattern="*.txt")
        names = {r.name for r in results}
        assert "deep.txt" in names


def test_walk_engine_max_depth():
    """WalkEngine 深度限制生效。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        deep = Path(tmpdir) / "a" / "b" / "c" / "d" / "e" / "f"
        deep.mkdir(parents=True)
        (deep / "too_deep.txt").write_text("deep")

        results = _walk_parsed(tmpdir, pattern="*.txt", max_depth=3)
        names = {r.name for r in results}
        assert "too_deep.txt" not in names

        results = _walk_parsed(tmpdir, pattern="*.txt", max_depth=10)
        names = {r.name for r in results}
        assert "too_deep.txt" in names


def test_walk_engine_max_results():
    """WalkEngine max_results 限制。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(20):
            (Path(tmpdir) / f"file_{i:02d}.txt").write_text(str(i))

        results = _walk_parsed(tmpdir, pattern="*.txt", max_results=5)
        assert len(results) <= 5


def test_walk_engine_result_fields():
    """WalkEngine 返回结果字段完整性。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "test.pdf").write_bytes(b"x" * 42)

        results = _walk_parsed(tmpdir, pattern="*.pdf", max_results=10)
        assert len(results) == 1
        r = results[0]
        assert r.name == "test.pdf"
        assert r.full_path.endswith("test.pdf")
        assert r.size == 42
        assert r.modified


def test_walk_engine_no_result():
    """WalkEngine 搜索不存在的文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = _walk_parsed(tmpdir, pattern="nonexistent_12345.xyz")
        assert len(results) == 0


# ============================================================
#  EverythingEngine: 需要 Everything 运行
# ============================================================

def test_everything_engine_available():
    """Everything 引擎是否可用。"""
    engine = EverythingEngine()
    try:
        parsed = ParsedSearch(pattern="*.exe")
        results = engine.search(parsed, max_results=1)
        available = len(results) > 0
    except Exception:
        available = False
    print(f"  Everything 可用: {available}")
    if not available:
        print("  跳过 Everything 测试（未安装或未运行）")
    return available


def test_everything_engine_search():
    """Everything 引擎基本搜索。"""
    engine = EverythingEngine()
    parsed = ParsedSearch(pattern="*.exe")
    results = engine.search(parsed, max_results=5)
    if not results:
        print("  跳过：Everything 不可用")
        return
    assert len(results) > 0
    r = results[0]
    assert r.name
    assert r.full_path
    assert r.size >= 0


# ============================================================
#  FallbackSearcher: 降级链
# ============================================================

def test_fallback_with_walk():
    """降级链：只用 WalkEngine（不依赖 Everything）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "test.txt").write_text("hello")

        searcher = FallbackSearcher(engines=[WalkEngine()])
        parsed = ParsedSearch(search_path=tmpdir, pattern="*.txt")
        results = searcher.search(parsed, max_results=100, max_depth=5)
        assert len(results) == 1
        assert results[0].name == "test.txt"


def test_fallback_degradation():
    """降级链：第一个引擎失败，第二个接管。"""

    class FailEngine:
        name = "fail"

        def search(self, parsed, max_results=500, max_depth=10):
            raise RuntimeError("模拟故障")

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "ok.txt").write_text("ok")

        searcher = FallbackSearcher(engines=[FailEngine(), WalkEngine()])
        parsed = ParsedSearch(search_path=tmpdir, pattern="*.txt")
        results = searcher.search(parsed, max_results=100, max_depth=5)
        assert len(results) == 1
        assert results[0].name == "ok.txt"


def test_fallback_all_fail():
    """降级链：所有引擎都失败，返回空列表。"""

    class FailEngine:
        name = "fail"

        def search(self, parsed, max_results=500, max_depth=10):
            raise RuntimeError("模拟故障")

    searcher = FallbackSearcher(engines=[FailEngine(), FailEngine()])
    parsed = ParsedSearch()
    results = searcher.search(parsed, max_results=100)
    assert results == []


def test_fallback_retry():
    """降级链：重试逻辑。"""
    call_count = 0

    class FlakyEngine:
        name = "flaky"

        def search(self, parsed, max_results=500, max_depth=10):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("临时故障")
            return [SearchResult(name="found.txt", path="C:\\", full_path="C:\\found.txt", size=100, modified="2024-01-01 00:00:00")]

    searcher = FallbackSearcher(engines=[FlakyEngine()], max_retries=3)
    parsed = ParsedSearch(pattern="test")
    results = searcher.search(parsed, max_results=100)
    assert len(results) == 1
    assert call_count == 3


def test_fallback_timeout_does_not_wait_for_slow_engine():
    """超时后不等待慢引擎结束，应快速切换到下一个引擎。"""
    import time

    class SlowEngine:
        name = "slow"

        def search(self, parsed, max_results=500, max_depth=10):
            time.sleep(0.3)
            return []

    class FastEngine:
        name = "fast"

        def search(self, parsed, max_results=500, max_depth=10):
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
    parsed = ParsedSearch(pattern="test")
    results = searcher.search(parsed, max_results=100)
    elapsed = time.perf_counter() - start

    assert len(results) == 1
    assert results[0].name == "found.txt"
    assert elapsed < 0.2


# ============================================================
#  search_files 工具: 端到端
# ============================================================

def test_search_files_tool():
    """search_files 工具端到端测试。"""
    try:
        from backend.tools.search_files import search_files
    except ImportError:
        print("  跳过：langchain_core 未安装")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "doc.pdf").write_bytes(b"pdf content")
        (Path(tmpdir) / "img.jpg").write_bytes(b"jpg content")

        result = search_files.invoke({
            "query": "*.pdf",
            "search_path": tmpdir,
            "max_results": 10,
            "max_depth": 5,
        })
        # search_files 现在返回 SearchResultHandle（句柄 + 预览）
        assert hasattr(result, "result_id")
        assert hasattr(result, "preview")
        names = {r.name for r in result.preview}
        assert "doc.pdf" in names
        assert "img.jpg" not in names


def test_search_files_no_result():
    """search_files 搜索不存在的文件。"""
    try:
        from backend.tools.search_files import search_files
    except ImportError:
        print("  跳过：langchain_core 未安装")
        return

    result = search_files.invoke({
        "query": "nonexistent_12345.xyz",
        "search_path": "C:\\",
        "max_results": 10,
        "max_depth": 1,
    })
    # search_files 现在返回 SearchResultHandle（句柄 + 预览）
    assert hasattr(result, "result_id")
    assert hasattr(result, "preview")
    assert len(result.preview) == 0


# ============================================================
#  运行
# ============================================================

if __name__ == "__main__":
    tests = [
        # build_query
        ("build_query: 纯关键词", test_build_query_keyword),
        ("build_query: 通配符", test_build_query_wildcard),
        ("build_query: 路径", test_build_query_path),
        ("build_query: 路径斜杠转换", test_build_query_path_slash),
        ("build_query: 单扩展名", test_build_query_extension_single),
        ("build_query: 多扩展名", test_build_query_extension_multiple),
        ("build_query: 扩展名无点", test_build_query_extension_no_dot),
        ("build_query: 大小范围", test_build_query_size),
        ("build_query: 只有最小大小", test_build_query_size_only_min),
        ("build_query: 时间范围", test_build_query_date_range),
        ("build_query: 只有起始时间", test_build_query_date_after_only),
        ("build_query: 只有结束时间", test_build_query_date_before_only),
        ("build_query: 全条件组合", test_build_query_full),
        # _parse_query
        ("parse_query: 关键词", test_parse_query_keyword),
        ("parse_query: 通配符", test_parse_query_wildcard),
        ("parse_query: 路径", test_parse_query_path),
        ("parse_query: 扩展名", test_parse_query_ext),
        ("parse_query: 最大大小", test_parse_query_size_gt),
        ("parse_query: 最小大小", test_parse_query_size_lt),
        ("parse_query: 时间范围", test_parse_query_dm_range),
        ("parse_query: 只有起始", test_parse_query_dm_after_only),
        ("parse_query: 多条件组合", test_parse_query_combined),
        # 辅助函数
        ("辅助: 大小解析", test_parse_size),
        ("辅助: dm 解析", test_parse_dm),
        ("辅助: 扩展名匹配", test_match_extensions),
        ("辅助: 大小匹配", test_match_size),
        ("辅助: 时间匹配", test_match_modified),
        # WalkEngine
        ("WalkEngine: 基本搜索", test_walk_engine_basic),
        ("WalkEngine: 扩展名过滤", test_walk_engine_extension_filter),
        ("WalkEngine: 大小过滤", test_walk_engine_size_filter),
        ("WalkEngine: 子目录", test_walk_engine_subdir),
        ("WalkEngine: 深度限制", test_walk_engine_max_depth),
        ("WalkEngine: 结果数量限制", test_walk_engine_max_results),
        ("WalkEngine: 字段完整性", test_walk_engine_result_fields),
        ("WalkEngine: 无结果", test_walk_engine_no_result),
        # EverythingEngine
        ("Everything: 可用性", test_everything_engine_available),
        ("Everything: 基本搜索", test_everything_engine_search),
        # FallbackSearcher
        ("降级链: WalkEngine", test_fallback_with_walk),
        ("降级链: 引擎失败降级", test_fallback_degradation),
        ("降级链: 全部失败", test_fallback_all_fail),
        ("降级链: 重试", test_fallback_retry),
        # search_files 工具
        ("工具: 端到端", test_search_files_tool),
        ("工具: 无结果", test_search_files_no_result),
    ]

    passed = 0
    failed = 0
    for name, test in tests:
        try:
            print(f"--- {name} ---")
            test()
            passed += 1
            print("PASS\n")
        except Exception as e:
            print(f"FAIL: {e}\n")
            failed += 1

    print(f"结果: {passed} 通过, {failed} 失败")
