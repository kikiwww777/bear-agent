"""find_duplicates 工具测试。覆盖：过滤规则、分组、端到端（Handle 模式）、分页。"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from backend.tools.find_duplicates import (
    _compute_md5,
    _should_skip,
    _group_by_size,
    _build_result,
    find_duplicates,
)
from backend.utils.format import format_size as _format_size
from backend.models import DuplicateResult, SearchResult


passed = 0
failed = 0


def run_test(name, func):
    global passed, failed
    print(f"--- {name} ---")
    try:
        func()
        passed += 1
        print("  PASS\n")
    except Exception as e:
        failed += 1
        print(f"  FAIL: {e}\n")
        traceback.print_exc()
        print()


# ============================================================
#  _compute_md5
# ============================================================

def test_md5_basic():
    """基本 MD5 计算。"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"hello world")
        tmp = Path(f.name)
    h = _compute_md5(tmp)
    os.unlink(tmp)
    assert h is not None and len(h) == 32, f"MD5 格式错误: {h}"
    print(f"  MD5: {h}")


def test_md5_nonexistent():
    """不存在的文件返回 None。"""
    h = _compute_md5(Path("C:\\nonexistent_file_12345.txt"))
    assert h is None


def test_md5_empty_file():
    """空文件的 MD5。"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        tmp = Path(f.name)
    h = _compute_md5(tmp)
    os.unlink(tmp)
    assert h is not None, "空文件应有 MD5"
    print(f"  空文件 MD5: {h}")


# ============================================================
#  _format_size
# ============================================================

def test_format_size():
    """大小格式化。"""
    assert _format_size(0) == "0 bytes"
    assert _format_size(512) == "512 bytes"
    assert _format_size(1024) == "1.0 KB"
    assert _format_size(1536) == "1.5 KB"
    assert _format_size(1048576) == "1.0 MB"
    assert _format_size(1073741824) == "1.0 GB"
    print("  各单位转换正确")


# ============================================================
#  _should_skip
# ============================================================

def test_skip_cache():
    """缓存目录应跳过。"""
    assert _should_skip(r"C:\Users\test\Cache\index") is True
    assert _should_skip(r"C:\Users\test\Code Cache\js\index") is True


def test_skip_chrome():
    """Chrome 文件应跳过。"""
    assert _should_skip(r"C:\Users\test\Chrome\Data\Default\Extensions\bg.js") is True
    assert _should_skip(r"C:\Users\test\Google Chrome\User Data\Default\Login Data") is True


def test_skip_system():
    """系统文件应跳过。"""
    assert _should_skip(r"C:\Users\test\desktop.ini") is True
    assert _should_skip(r"C:\Users\test\Thumbs.db") is True
    assert _should_skip(r"C:\Users\test\__init__.py") is True


def test_skip_extensions():
    """黑名单扩展名应跳过。"""
    assert _should_skip(r"C:\Users\test\app.pyc") is True
    assert _should_skip(r"C:\Users\test\debug.log") is True
    assert _should_skip(r"C:\Users\test\cache.tmp") is True


def test_skip_normal():
    """正常文件不应跳过。"""
    assert _should_skip(r"C:\Users\test\report.pdf") is False
    assert _should_skip(r"C:\Users\test\photo.jpg") is False
    assert _should_skip(r"C:\Users\test\homework.docx") is False


# ============================================================
#  _group_by_size
# ============================================================

def test_group_basic():
    """基本分组。"""
    results = [
        SearchResult(name="a.txt", path="C:\\", full_path="C:\\a.txt", size=100, modified="2024-01-01 00:00:00"),
        SearchResult(name="b.txt", path="C:\\", full_path="C:\\b.txt", size=100, modified="2024-01-01 00:00:00"),
        SearchResult(name="c.txt", path="C:\\", full_path="C:\\c.txt", size=200, modified="2024-01-01 00:00:00"),
    ]
    groups = _group_by_size(results)
    assert len(groups) == 1
    assert 100 in groups
    assert len(groups[100]) == 2


def test_group_filter():
    """被过滤的文件不参与分组。"""
    results = [
        SearchResult(name="a.txt", path="C:\\", full_path="C:\\a.txt", size=100, modified="2024-01-01 00:00:00"),
        SearchResult(name="b.pyc", path="C:\\", full_path="C:\\b.pyc", size=100, modified="2024-01-01 00:00:00"),
    ]
    groups = _group_by_size(results)
    # b.pyc 被过滤，只剩 a.txt，不够一组
    assert len(groups) == 0


def test_group_empty():
    """空列表返回空结果。"""
    assert _group_by_size([]) == {}


# ============================================================
#  _build_result
# ============================================================

def test_build_result_fields():
    """_build_result 生成的 DuplicateResult 包含 wasted 字段。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建真实文件（_build_result 需要读取文件算 MD5）
        content = b"x" * 1048576
        (Path(tmpdir) / "a.pdf").write_bytes(content)
        (Path(tmpdir) / "b.pdf").write_bytes(content)

        results = [
            SearchResult(name="a.pdf", path=tmpdir, full_path=str(Path(tmpdir) / "a.pdf"),
                         size=1048576, modified="2024-01-01 00:00:00"),
            SearchResult(name="b.pdf", path=tmpdir, full_path=str(Path(tmpdir) / "b.pdf"),
                         size=1048576, modified="2024-01-01 00:00:00"),
        ]
        size_groups = {1048576: results}
        duplicates = _build_result(size_groups)
        assert len(duplicates) == 1
        d = duplicates[0]
        assert d.size == 1048576
        assert d.count == 2
        assert d.wasted == 1048576
        assert len(d.files) == 2


# ============================================================
#  find_duplicates 模式 1：新搜索（Handle 模式）
# ============================================================

def test_e2e_basic():
    """端到端：有重复文件，返回 DuplicateGroupHandle。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "dup1.txt").write_text("same content here")
        (Path(tmpdir) / "dup2.txt").write_text("same content here")
        (Path(tmpdir) / "unique.txt").write_text("different content")

        handle = find_duplicates.invoke({"search_path": tmpdir, "min_size": 1, "max_results": 100})

        assert handle.total_groups == 1, f"期望 1 组，实际 {handle.total_groups} 组"
        assert handle.result_id.startswith("dup_"), f"result_id 前缀错误: {handle.result_id}"
        assert len(handle.preview) == 1
        assert handle.has_more is False

        d = handle.preview[0]
        assert d.count == 2
        assert d.wasted > 0
        assert len(d.files) == 2
        assert d.files[0].full_path  # 有完整路径
        print(f"  result_id={handle.result_id}, files={len(d.files)}, wasted={d.wasted}")


def test_e2e_no_duplicates():
    """端到端：无重复文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "a.txt").write_text("content A")
        (Path(tmpdir) / "b.txt").write_text("content B")

        handle = find_duplicates.invoke({"search_path": tmpdir, "min_size": 1, "max_results": 100})
        assert handle.total_groups == 0
        assert handle.result_id == ""
        assert len(handle.preview) == 0


def test_e2e_empty_dir():
    """端到端：空目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        handle = find_duplicates.invoke({"search_path": tmpdir, "min_size": 1, "max_results": 100})
        assert handle.total_groups == 0
        assert handle.result_id == ""


def test_e2e_filtered():
    """端到端：重复文件被过滤规则跳过。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建缓存目录下的重复文件（应被过滤）
        cache_dir = Path(tmpdir) / "Cache"
        cache_dir.mkdir()
        (cache_dir / "a.dat").write_bytes(b"\x00" * 100)
        (cache_dir / "b.dat").write_bytes(b"\x00" * 100)

        handle = find_duplicates.invoke({"search_path": tmpdir, "min_size": 1, "max_results": 100})
        assert handle.total_groups == 0, f"缓存文件应被过滤，但返回了 {handle.total_groups} 组"


def test_e2e_result_fields():
    """端到端：DuplicateResult 字段完整性。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "a.pdf").write_bytes(b"x" * 2048)
        (Path(tmpdir) / "b.pdf").write_bytes(b"x" * 2048)
        (Path(tmpdir) / "c.pdf").write_bytes(b"x" * 2048)

        handle = find_duplicates.invoke({"search_path": tmpdir, "min_size": 1, "max_results": 100})

        assert handle.total_groups == 1
        d = handle.preview[0]
        assert d.hash and len(d.hash) == 32
        assert d.size == 2048
        assert d.count == 3
        assert d.wasted == 2048 * 2
        assert len(d.files) == 3
        # 每个文件都有完整路径
        for f in d.files:
            assert f.full_path
            assert f.name


# ============================================================
#  find_duplicates 模式 2：分页
# ============================================================

def test_pagination_invalid():
    """分页查询：无效 result_id 返回空。"""
    page = find_duplicates.invoke({"result_id": "dup_nonexistent", "page": 1})
    assert page.total_groups == 0
    assert len(page.results) == 0
    assert page.has_more is False


def test_pagination_returns_duplicate_result():
    """分页查询：返回的是 DuplicateResult（含文件路径）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建足够多的重复组来触发分页
        for i in range(25):
            (Path(tmpdir) / f"a{i}.txt").write_text(f"content {i}")
            (Path(tmpdir) / f"b{i}.txt").write_text(f"content {i}")

        handle = find_duplicates.invoke({"search_path": tmpdir, "min_size": 1, "max_results": 100})

        assert handle.total_groups == 25
        assert handle.has_more is True  # 25 > 20, 应该有更多页

        # 获取第二页
        page = find_duplicates.invoke({"result_id": handle.result_id, "page": 2})
        assert len(page.results) == 5  # 25 - 20 = 5
        assert page.has_more is False

        # 验证返回的是 DuplicateResult，有文件路径
        d = page.results[0]
        assert d.hash
        assert d.files
        assert d.files[0].full_path
        assert d.wasted >= 0


# ============================================================
#  运行
# ============================================================

if __name__ == "__main__":
    tests = [
        ("MD5: 基本计算", test_md5_basic),
        ("MD5: 不存在文件", test_md5_nonexistent),
        ("MD5: 空文件", test_md5_empty_file),
        ("格式化: 大小转换", test_format_size),
        ("过滤: 缓存目录", test_skip_cache),
        ("过滤: Chrome 文件", test_skip_chrome),
        ("过滤: 系统文件", test_skip_system),
        ("过滤: 黑名单扩展名", test_skip_extensions),
        ("过滤: 正常文件", test_skip_normal),
        ("分组: 基本分组", test_group_basic),
        ("分组: 过滤后分组", test_group_filter),
        ("分组: 空列表", test_group_empty),
        ("构建: 字段完整性", test_build_result_fields),
        ("端到端: 有重复", test_e2e_basic),
        ("端到端: 无重复", test_e2e_no_duplicates),
        ("端到端: 空目录", test_e2e_empty_dir),
        ("端到端: 文件被过滤", test_e2e_filtered),
        ("端到端: 字段完整性", test_e2e_result_fields),
        ("分页: 无效句柄", test_pagination_invalid),
        ("分页: 返回含路径", test_pagination_returns_duplicate_result),
    ]

    for name, test in tests:
        run_test(name, test)

    print(f"结果: {passed} 通过, {failed} 失败")
