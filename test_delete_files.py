"""delete_files 工具测试。

使用 Handle 模式和 sources 模式两种方式测试。
"""
import tempfile
from pathlib import Path

from backend.models import FileOperationResult
from backend.tools.search_files import search_files
from backend.tools.delete_files import delete_files


class TestDeleteFiles:
    """delete_files 工具测试（Handle 模式）。"""

    def test_invalid_result_id(self):
        """无效的 result_id。"""
        result = delete_files.invoke({"result_id": "invalid_id"})
        assert result.total_files == 0
        assert result.success_count == 0

    def test_no_params(self):
        """不传 result_id 和 sources。"""
        result = delete_files.invoke({})
        assert result.total_files == 0
        assert result.success_count == 0


class TestDeleteFilesWithSources:
    """delete_files 工具测试（sources 模式）。"""

    def test_delete_single_file(self):
        """删除单个文件到回收站。"""
        with tempfile.TemporaryDirectory() as parent:
            test_file = Path(parent) / "to_delete.txt"
            test_file.write_text("delete me")

            result = delete_files.invoke({
                "sources": [str(test_file)],
            })

            assert result.total_files == 1
            assert result.success_count == 1
            assert result.fail_count == 0
            # 默认移到回收站，源文件应不存在
            assert not test_file.exists()

    def test_delete_multiple_files(self):
        """删除多个文件。"""
        with tempfile.TemporaryDirectory() as parent:
            files = []
            for i in range(5):
                f = Path(parent) / f"file_{i}.txt"
                f.write_text(f"content {i}")
                files.append(str(f))

            result = delete_files.invoke({"sources": files})

            assert result.total_files == 5
            assert result.success_count == 5
            assert result.fail_count == 0
            assert result.total_size > 0

    def test_delete_nonexistent_file(self):
        """删除不存在的文件。"""
        result = delete_files.invoke({
            "sources": ["C:\\nonexistent\\file.txt"],
        })

        assert result.total_files == 0
        assert result.success_count == 0

    def test_result_fields(self):
        """结果字段完整性。"""
        with tempfile.TemporaryDirectory() as parent:
            test_file = Path(parent) / "test.txt"
            test_file.write_text("hello")

            result = delete_files.invoke({
                "sources": [str(test_file)],
            })

            assert isinstance(result, FileOperationResult)
            assert isinstance(result.total_files, int)
            assert isinstance(result.total_size, int)
            assert isinstance(result.success_count, int)
            assert isinstance(result.fail_count, int)
            assert isinstance(result.failed_files, list)
