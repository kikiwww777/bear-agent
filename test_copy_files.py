"""copy_files 工具测试。

使用 Handle 模式：search_files 返回句柄，copy_files 通过句柄获取结果。
"""
import tempfile
from pathlib import Path

import pytest

from backend.models import FileOperationResult
from backend.tools.search_files import search_files
from backend.tools.copy_files import copy_files


# ============================================================
#  copy_files 工具测试（Handle 模式）
# ============================================================

class TestCopyFiles:
    """copy_files 工具集成测试。"""

    def test_invalid_result_id(self):
        """无效的 result_id。"""
        result = copy_files.invoke({
            "result_id": "invalid_id",
            "target_dir": "C:\\temp\\backup",
        })
        assert result.total_files == 0
        assert result.success_count == 0
        assert result.fail_count == 0

    def test_copy_single_file(self):
        """复制单个文件。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建测试文件
                test_file = Path(source_dir) / "test.txt"
                test_file.write_text("hello")

                # 搜索文件
                handle = search_files.invoke({"query": "*", "search_path": source_dir, "max_results": 100})

                # 复制文件
                result = copy_files.invoke({
                    "result_id": handle.result_id,
                    "target_dir": target_dir,
                })

                assert result.total_files >= 1
                assert result.success_count >= 1
                assert result.fail_count == 0

                # 检查文件是否复制成功
                target_file = Path(target_dir) / "test.txt"
                assert target_file.exists()
                assert target_file.read_text() == "hello"

    def test_copy_multiple_files(self):
        """复制多个文件。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建多个测试文件
                for i in range(5):
                    test_file = Path(source_dir) / f"test_{i}.txt"
                    test_file.write_text(f"content {i}")

                # 搜索文件
                handle = search_files.invoke({"query": "*", "search_path": source_dir, "max_results": 100})

                # 复制文件
                result = copy_files.invoke({
                    "result_id": handle.result_id,
                    "target_dir": target_dir,
                })

                assert result.total_files == 5
                assert result.success_count == 5
                assert result.fail_count == 0
                assert result.total_size > 0

    def test_copy_with_extension_filter(self):
        """按扩展名过滤复制。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建不同类型的文件
                (Path(source_dir) / "doc.pdf").write_text("pdf")
                (Path(source_dir) / "image.jpg").write_text("jpg")
                (Path(source_dir) / "text.txt").write_text("txt")

                # 搜索 txt 文件
                handle = search_files.invoke({"query": "*.txt", "search_path": source_dir, "max_results": 100})

                # 复制文件
                result = copy_files.invoke({
                    "result_id": handle.result_id,
                    "target_dir": target_dir,
                })

                # 只复制 txt 文件
                assert (Path(target_dir) / "text.txt").exists()
                assert not (Path(target_dir) / "doc.pdf").exists()
                assert not (Path(target_dir) / "image.jpg").exists()

    def test_result_fields(self):
        """结果字段完整性。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建测试文件
                (Path(source_dir) / "test.txt").write_text("hello")

                # 搜索文件
                handle = search_files.invoke({"query": "*", "search_path": source_dir, "max_results": 100})

                # 复制文件
                result = copy_files.invoke({
                    "result_id": handle.result_id,
                    "target_dir": target_dir,
                })

                # 检查结果字段
                assert isinstance(result, FileOperationResult)
                assert isinstance(result.total_files, int)
                assert isinstance(result.total_size, int)
                assert isinstance(result.success_count, int)
                assert isinstance(result.fail_count, int)
                assert isinstance(result.failed_files, list)


class TestCopyFilesWithSources:
    """copy_files 工具测试（sources 模式）。"""

    def test_copy_single_file(self):
        """复制单个文件。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建测试文件
                test_file = Path(source_dir) / "test.txt"
                test_file.write_text("hello")

                # 直接传路径列表
                result = copy_files.invoke({
                    "sources": [str(test_file)],
                    "target_dir": target_dir,
                })

                assert result.total_files == 1
                assert result.success_count == 1
                assert result.fail_count == 0

                # 检查文件是否复制成功
                target_file = Path(target_dir) / "test.txt"
                assert target_file.exists()
                assert target_file.read_text() == "hello"

    def test_copy_multiple_files(self):
        """复制多个文件。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建多个测试文件
                files = []
                for i in range(5):
                    test_file = Path(source_dir) / f"test_{i}.txt"
                    test_file.write_text(f"content {i}")
                    files.append(str(test_file))

                # 直接传路径列表
                result = copy_files.invoke({
                    "sources": files,
                    "target_dir": target_dir,
                })

                assert result.total_files == 5
                assert result.success_count == 5
                assert result.fail_count == 0

    def test_copy_nonexistent_file(self):
        """复制不存在的文件。"""
        with tempfile.TemporaryDirectory() as target_dir:
            result = copy_files.invoke({
                "sources": ["C:\\nonexistent\\file.txt"],
                "target_dir": target_dir,
            })

            # 不存在的文件会被跳过
            assert result.total_files == 0
            assert result.success_count == 0

    def test_no_params(self):
        """不传参数。"""
        with tempfile.TemporaryDirectory() as target_dir:
            result = copy_files.invoke({
                "target_dir": target_dir,
            })

            # 必须传 result_id 或 sources
            assert result.total_files == 0
            assert result.success_count == 0
