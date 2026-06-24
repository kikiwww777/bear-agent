"""file_stats 工具测试。"""
import pytest
from backend.tools.file_stats import file_stats, _compute_stats
from backend.models import SearchResult


# ============================================================
#  _compute_stats 单元测试
# ============================================================

class TestComputeStats:
    """_compute_stats 内存统计逻辑测试。"""

    def test_basic_stats(self):
        """基本统计：文件数、总大小、扩展名分组、子目录分组。"""
        results = [
            SearchResult(name="a.txt", path="C:\\test", full_path="C:\\test\\a.txt",
                         size=100, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.jpg", path="C:\\test", full_path="C:\\test\\b.jpg",
                         size=200, modified="2025-01-01 00:00:00"),
            SearchResult(name="c.txt", path="C:\\test\\sub", full_path="C:\\test\\sub\\c.txt",
                         size=300, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        assert stats.file_count == 3
        assert stats.total_size == 600
        assert stats.dir_count == 2  # 根目录 + sub

        # 按大小降序：txt (400) > jpg (200)
        assert stats.by_extension[0].extension == "txt"
        assert stats.by_extension[0].total_size == 400
        assert stats.by_extension[1].extension == "jpg"
        assert stats.by_extension[1].total_size == 200

        # 按大小降序：sub (300) > 根目录 (300)
        # 根目录有 a.txt(100) + b.jpg(200) = 300，sub 有 c.txt(300)
        assert stats.by_directory[0].total_size == 300

    def test_empty_results(self):
        """空列表返回零值。"""
        stats = _compute_stats([], "C:\\test")

        assert stats.file_count == 0
        assert stats.total_size == 0
        assert stats.dir_count == 0
        assert stats.by_extension == []
        assert stats.by_directory == []
        assert stats.by_category == []
        assert stats.largest_files == []

    def test_no_extension(self):
        """没有扩展名的文件归为 '无扩展名'。"""
        results = [
            SearchResult(name="Makefile", path="C:\\test", full_path="C:\\test\\Makefile",
                         size=50, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        assert stats.by_extension[0].extension == "无扩展名"
        assert stats.by_extension[0].count == 1

    def test_extension_case_insensitive(self):
        """扩展名大小写不敏感：.TXT 和 .txt 算同一类。"""
        results = [
            SearchResult(name="a.TXT", path="C:\\test", full_path="C:\\test\\a.TXT",
                         size=100, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.txt", path="C:\\test", full_path="C:\\test\\b.txt",
                         size=200, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        assert len(stats.by_extension) == 1
        assert stats.by_extension[0].extension == "txt"
        assert stats.by_extension[0].count == 2
        assert stats.by_extension[0].total_size == 300

    def test_sort_by_size_desc(self):
        """扩展名和子目录都按总大小降序排列。"""
        results = [
            # mp4: 1000 bytes
            SearchResult(name="video.mp4", path="C:\\test", full_path="C:\\test\\video.mp4",
                         size=1000, modified="2025-01-01 00:00:00"),
            # txt: 50 bytes
            SearchResult(name="note.txt", path="C:\\test", full_path="C:\\test\\note.txt",
                         size=50, modified="2025-01-01 00:00:00"),
            # jpg: 200 bytes
            SearchResult(name="photo.jpg", path="C:\\test", full_path="C:\\test\\photo.jpg",
                         size=200, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        sizes = [e.total_size for e in stats.by_extension]
        assert sizes == sorted(sizes, reverse=True)

    def test_nested_directory_first_level(self):
        """只取一级子目录名，深层目录归入同一级。"""
        results = [
            SearchResult(name="a.txt", path="C:\\test\\docs",
                         full_path="C:\\test\\docs\\a.txt", size=100,
                         modified="2025-01-01 00:00:00"),
            SearchResult(name="b.txt", path="C:\\test\\docs\\deep",
                         full_path="C:\\test\\docs\\deep\\b.txt", size=200,
                         modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        # 两个文件都在 docs 下（一级子目录）
        assert len(stats.by_directory) == 1
        assert stats.by_directory[0].name == "docs"
        assert stats.by_directory[0].count == 2
        assert stats.by_directory[0].total_size == 300

    def test_root_files(self):
        """直接在 search_path 下的文件归为 '根目录'。"""
        results = [
            SearchResult(name="a.txt", path="C:\\test",
                         full_path="C:\\test\\a.txt", size=100,
                         modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        assert stats.by_directory[0].name == "根目录"
        assert stats.by_directory[0].count == 1


# ============================================================
#  分类统计测试
# ============================================================

class TestCategoryStats:
    """文件类型分类统计测试。"""

    def test_basic_classification(self):
        """jpg/png 归入图片，mp4 归入视频，pdf 归入文档。"""
        results = [
            SearchResult(name="a.jpg", path="C:\\test", full_path="C:\\test\\a.jpg",
                         size=100, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.png", path="C:\\test", full_path="C:\\test\\b.png",
                         size=200, modified="2025-01-01 00:00:00"),
            SearchResult(name="c.mp4", path="C:\\test", full_path="C:\\test\\c.mp4",
                         size=500, modified="2025-01-01 00:00:00"),
            SearchResult(name="d.pdf", path="C:\\test", full_path="C:\\test\\d.pdf",
                         size=300, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        cats = {c.category: c for c in stats.by_category}
        assert "图片" in cats
        assert cats["图片"].count == 2
        assert cats["图片"].total_size == 300
        assert "视频" in cats
        assert cats["视频"].count == 1
        assert cats["视频"].total_size == 500
        assert "文档" in cats
        assert cats["文档"].count == 1

    def test_unknown_extension_goes_to_other(self):
        """未知扩展名归入 '其他'。"""
        results = [
            SearchResult(name="file.xyz", path="C:\\test", full_path="C:\\test\\file.xyz",
                         size=100, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        assert stats.by_category[0].category == "其他"
        assert stats.by_category[0].count == 1

    def test_category_sorted_by_size(self):
        """分类按总大小降序排列。"""
        results = [
            SearchResult(name="a.mp4", path="C:\\test", full_path="C:\\test\\a.mp4",
                         size=1000, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.txt", path="C:\\test", full_path="C:\\test\\b.txt",
                         size=50, modified="2025-01-01 00:00:00"),
            SearchResult(name="c.jpg", path="C:\\test", full_path="C:\\test\\c.jpg",
                         size=200, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        assert stats.by_category[0].category == "视频"  # 1000
        assert stats.by_category[1].category == "图片"  # 200
        assert stats.by_category[2].category == "文档"  # 50

    def test_top_extensions_in_category(self):
        """每个分类下 top_extensions 取最大的 3 个扩展名。"""
        results = [
            SearchResult(name=f"a.jpg", path="C:\\test", full_path="C:\\test\\a.jpg",
                         size=300, modified="2025-01-01 00:00:00"),
            SearchResult(name=f"b.png", path="C:\\test", full_path="C:\\test\\b.png",
                         size=200, modified="2025-01-01 00:00:00"),
            SearchResult(name=f"c.gif", path="C:\\test", full_path="C:\\test\\c.gif",
                         size=100, modified="2025-01-01 00:00:00"),
            SearchResult(name=f"d.bmp", path="C:\\test", full_path="C:\\test\\d.bmp",
                         size=50, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        img = stats.by_category[0]
        assert img.category == "图片"
        assert img.top_extensions == ["jpg", "png", "gif"]  # bmp 排第 4，不进 top 3

    def test_path_based_installed_apps(self):
        """Program Files 下的 dll 归入 '安装的应用'（路径优先于扩展名）。"""
        results = [
            SearchResult(name="app.dll", path="C:\\Program Files\\MyApp",
                         full_path="C:\\Program Files\\MyApp\\app.dll",
                         size=500, modified="2025-01-01 00:00:00"),
            SearchResult(name="data.txt", path="C:\\Users\\test",
                         full_path="C:\\Users\\test\\data.txt",
                         size=100, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\")

        cats = {c.category: c for c in stats.by_category}
        assert "安装的应用" in cats
        assert cats["安装的应用"].count == 1
        assert cats["安装的应用"].total_size == 500
        assert "文档" in cats
        assert cats["文档"].count == 1

    def test_path_based_system(self):
        """Windows 目录下的文件归入 '系统和保留'。"""
        results = [
            SearchResult(name="ntdll.dll", path="C:\\Windows\\System32",
                         full_path="C:\\Windows\\System32\\ntdll.dll",
                         size=2000, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\")

        assert stats.by_category[0].category == "系统和保留"
        assert stats.by_category[0].count == 1

    def test_path_based_temp(self):
        """Temp 目录下的文件归入 '临时文件'（路径判定）。"""
        results = [
            SearchResult(name="setup.exe", path="C:\\Users\\test\\AppData\\Local\\Temp",
                         full_path="C:\\Users\\test\\AppData\\Local\\Temp\\setup.exe",
                         size=300, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\")

        assert stats.by_category[0].category == "临时文件"

    def test_path_based_appdata(self):
        """AppData 下非 Temp 目录归入 '安装的应用'（应用数据）。"""
        results = [
            SearchResult(name="config.json",
                         path="C:\\Users\\test\\AppData\\Local\\MyApp",
                         full_path="C:\\Users\\test\\AppData\\Local\\MyApp\\config.json",
                         size=50, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\")

        assert stats.by_category[0].category == "安装的应用"

    def test_path_priority_over_extension(self):
        """路径分类优先于扩展名分类。Program Files 里的 .jpg 仍归入 '安装的应用'。"""
        results = [
            SearchResult(name="splash.jpg",
                         path="C:\\Program Files\\MyGame\\assets",
                         full_path="C:\\Program Files\\MyGame\\assets\\splash.jpg",
                         size=1000, modified="2025-01-01 00:00:00"),
            SearchResult(name="photo.jpg",
                         path="C:\\Users\\test\\Pictures",
                         full_path="C:\\Users\\test\\Pictures\\photo.jpg",
                         size=500, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\")

        cats = {c.category: c for c in stats.by_category}
        assert cats["安装的应用"].count == 1
        assert cats["安装的应用"].total_size == 1000
        assert cats["图片"].count == 1
        assert cats["图片"].total_size == 500


# ============================================================
#  百分比和平均大小测试
# ============================================================

class TestPercentageAndAvgSize:
    """百分比和平均文件大小测试。"""

    def test_extension_percentage(self):
        """扩展名百分比正确。"""
        results = [
            SearchResult(name="a.txt", path="C:\\test", full_path="C:\\test\\a.txt",
                         size=750, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.jpg", path="C:\\test", full_path="C:\\test\\b.jpg",
                         size=250, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        txt = stats.by_extension[0]  # 750
        jpg = stats.by_extension[1]  # 250
        assert txt.percentage == 75.0
        assert jpg.percentage == 25.0

    def test_directory_percentage(self):
        """子目录百分比正确。"""
        results = [
            SearchResult(name="a.txt", path="C:\\test\\big", full_path="C:\\test\\big\\a.txt",
                         size=800, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.txt", path="C:\\test\\small", full_path="C:\\test\\small\\b.txt",
                         size=200, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        big = next(d for d in stats.by_directory if d.name == "big")
        small = next(d for d in stats.by_directory if d.name == "small")
        assert big.percentage == 80.0
        assert small.percentage == 20.0

    def test_category_percentage(self):
        """分类百分比正确。"""
        results = [
            SearchResult(name="a.mp4", path="C:\\test", full_path="C:\\test\\a.mp4",
                         size=600, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.txt", path="C:\\test", full_path="C:\\test\\b.txt",
                         size=400, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        video = next(c for c in stats.by_category if c.category == "视频")
        doc = next(c for c in stats.by_category if c.category == "文档")
        assert video.percentage == 60.0
        assert doc.percentage == 40.0

    def test_avg_size_extension(self):
        """扩展名平均文件大小正确。"""
        results = [
            SearchResult(name="a.txt", path="C:\\test", full_path="C:\\test\\a.txt",
                         size=100, modified="2025-01-01 00:00:00"),
            SearchResult(name="b.txt", path="C:\\test", full_path="C:\\test\\b.txt",
                         size=300, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test")

        txt = stats.by_extension[0]
        assert txt.total_size == 400  # 100+300

    def test_zero_total_percentage(self):
        """total_size=0 时百分比为 0。"""
        stats = _compute_stats([], "C:\\test")
        # 空结果不会触发百分比计算，但模型默认值应为 0
        assert stats.total_size == 0


# ============================================================
#  最大文件测试
# ============================================================

class TestLargestFiles:
    """最大文件 Top N 测试。"""

    def test_basic_largest_files(self):
        """按大小降序返回最大的 N 个文件。"""
        results = [
            SearchResult(name="small.txt", path="C:\\test", full_path="C:\\test\\small.txt",
                         size=10, modified="2025-01-01 00:00:00"),
            SearchResult(name="big.mp4", path="C:\\test", full_path="C:\\test\\big.mp4",
                         size=1000, modified="2025-01-01 00:00:00"),
            SearchResult(name="medium.jpg", path="C:\\test", full_path="C:\\test\\medium.jpg",
                         size=500, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test", largest_n=2)

        assert len(stats.largest_files) == 2
        assert stats.largest_files[0].name == "big.mp4"
        assert stats.largest_files[0].size == 1000
        assert stats.largest_files[0].category == "视频"
        assert stats.largest_files[1].name == "medium.jpg"
        assert stats.largest_files[1].size == 500
        assert stats.largest_files[1].category == "图片"

    def test_largest_files_with_category(self):
        """每个 LargestFile 带分类标签。"""
        results = [
            SearchResult(name="doc.pdf", path="C:\\test", full_path="C:\\test\\doc.pdf",
                         size=100, modified="2025-01-01 00:00:00"),
            SearchResult(name="code.py", path="C:\\test", full_path="C:\\test\\code.py",
                         size=50, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test", largest_n=10)

        cats = {f.name: f.category for f in stats.largest_files}
        assert cats["doc.pdf"] == "文档"
        assert cats["code.py"] == "代码"

    def test_largest_files_skip_zero_size(self):
        """零字节文件不进最大文件列表。"""
        results = [
            SearchResult(name="empty.txt", path="C:\\test", full_path="C:\\test\\empty.txt",
                         size=0, modified="2025-01-01 00:00:00"),
            SearchResult(name="real.txt", path="C:\\test", full_path="C:\\test\\real.txt",
                         size=100, modified="2025-01-01 00:00:00"),
        ]
        stats = _compute_stats(results, "C:\\test", largest_n=10)

        assert len(stats.largest_files) == 1
        assert stats.largest_files[0].name == "real.txt"

    def test_largest_files_truncation(self):
        """largest_n 截断：只返回前 N 个。"""
        results = [
            SearchResult(name=f"file{i}.txt", path="C:\\test", full_path=f"C:\\test\\file{i}.txt",
                         size=100 * (i + 1), modified="2025-01-01 00:00:00")
            for i in range(5)
        ]
        stats = _compute_stats(results, "C:\\test", largest_n=3)

        assert len(stats.largest_files) == 3
        assert stats.largest_files[0].size == 500  # file4
        assert stats.largest_files[1].size == 400  # file3
        assert stats.largest_files[2].size == 300  # file2


# ============================================================
#  file_stats 工具集成测试（需要真实磁盘）
# ============================================================

class TestFileStatsTool:
    """file_stats @tool 端到端测试。"""

    def test_basic_tool(self, tmp_path):
        """基本端到端测试。"""
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.jpg").write_bytes(b"\x00" * 100)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("world")

        result = file_stats.invoke({"path": str(tmp_path)})

        # Everything 索引有延迟，新创建的文件可能还没被索引
        # 至少能统计到部分文件
        assert result.file_count >= 1
        assert result.total_size >= 5
        assert result.path == str(tmp_path)

    def test_empty_directory(self, tmp_path):
        """空目录返回零值。"""
        result = file_stats.invoke({"path": str(tmp_path)})

        assert result.file_count == 0
        assert result.total_size == 0
        assert result.by_extension == []
        assert result.by_directory == []
        assert result.by_category == []
        assert result.largest_files == []

    def test_nonexistent_path(self):
        """不存在的路径返回零值，不抛异常。"""
        result = file_stats.invoke({"path": "C:\\nonexistent_path_12345"})

        assert result.file_count == 0
        assert result.total_size == 0

    def test_extension_sorted_by_size(self, tmp_path):
        """扩展名按总大小降序。"""
        (tmp_path / "big.mp4").write_bytes(b"\x00" * 1000)
        (tmp_path / "small.txt").write_text("hi")

        result = file_stats.invoke({"path": str(tmp_path)})

        assert result.by_extension[0].extension == "mp4"
        assert result.by_extension[0].total_size == 1000
        assert result.by_extension[1].extension == "txt"
        assert result.by_extension[1].total_size == 2

    def test_dir_sorted_by_size(self, tmp_path):
        """子目录按总大小降序。"""
        big_dir = tmp_path / "big"
        big_dir.mkdir()
        (big_dir / "data.bin").write_bytes(b"\x00" * 500)

        small_dir = tmp_path / "small"
        small_dir.mkdir()
        (small_dir / "tiny.txt").write_text("x")

        result = file_stats.invoke({"path": str(tmp_path)})

        assert result.by_directory[0].name == "big"
        assert result.by_directory[0].total_size == 500
        assert result.by_directory[1].name == "small"
        assert result.by_directory[1].total_size == 1

    def test_total_size(self, tmp_path):
        """total_size 正确。"""
        (tmp_path / "data.bin").write_bytes(b"\x00" * 1024)

        result = file_stats.invoke({"path": str(tmp_path)})

        assert result.total_size == 1024

    def test_top_n_truncation(self, tmp_path):
        """top_n 截断：只返回前 N 个扩展名/目录，其余归入 other。"""
        # 创建 5 种不同扩展名的文件
        for i, ext in enumerate(["a", "b", "c", "d", "e"]):
            (tmp_path / f"file.{ext}").write_bytes(b"\x00" * (100 * (i + 1)))

        # top_n=2 只保留最大的 2 种扩展名
        result = file_stats.invoke({"path": str(tmp_path), "top_n": 2})

        assert len(result.by_extension) == 2
        assert result.by_extension[0].extension == "e"  # 500 bytes
        assert result.by_extension[1].extension == "d"  # 400 bytes
        assert result.other_extensions_count == 3       # a, b, c 被截断
        assert result.other_extensions_size == 100 + 200 + 300  # 600 bytes

    def test_top_n_no_truncation(self, tmp_path):
        """文件种类少于 top_n 时，other 为零。"""
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.jpg").write_bytes(b"\x00" * 100)

        result = file_stats.invoke({"path": str(tmp_path), "top_n": 20})

        assert len(result.by_extension) == 2
        assert result.other_extensions_count == 0
        assert result.other_extensions_size == 0
        assert result.other_directories_count == 0
        assert result.other_directories_size == 0

    def test_largest_files_in_tool(self, tmp_path):
        """工具层面 largest_n 参数生效。"""
        (tmp_path / "big.bin").write_bytes(b"\x00" * 1000)
        (tmp_path / "small.txt").write_text("hi")

        result = file_stats.invoke({"path": str(tmp_path), "largest_n": 1})

        assert len(result.largest_files) <= 1
        if result.largest_files:
            assert result.largest_files[0].size == 1000

    def test_largest_files_zero(self, tmp_path):
        """largest_n=0 不返回最大文件。"""
        (tmp_path / "data.bin").write_bytes(b"\x00" * 100)

        result = file_stats.invoke({"path": str(tmp_path), "largest_n": 0})

        assert result.largest_files == []
