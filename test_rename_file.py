"""rename_file 工具测试。"""
import tempfile
from pathlib import Path

from backend.models import RenameResult
from backend.tools.rename_file import rename_file


class TestRenameFile:
    """rename_file 工具测试。"""

    def test_rename_file(self):
        """重命名单个文件。"""
        with tempfile.TemporaryDirectory() as parent:
            old_file = Path(parent) / "old.txt"
            old_file.write_text("hello")

            result = rename_file.invoke({
                "path": str(old_file),
                "new_name": "new.txt",
            })

            assert result.success is True
            assert result.error is None
            assert Path(result.new_path).exists()
            assert not old_file.exists()
            assert Path(result.new_path).read_text() == "hello"

    def test_rename_directory(self):
        """重命名目录。"""
        with tempfile.TemporaryDirectory() as parent:
            old_dir = Path(parent) / "old_folder"
            old_dir.mkdir()

            result = rename_file.invoke({
                "path": str(old_dir),
                "new_name": "new_folder",
            })

            assert result.success is True
            assert Path(result.new_path).is_dir()
            assert not old_dir.exists()

    def test_rename_nonexistent(self):
        """源路径不存在。"""
        result = rename_file.invoke({
            "path": "C:\\nonexistent\\file.txt",
            "new_name": "new.txt",
        })

        assert result.success is False
        assert result.error is not None
        assert "不存在" in result.error

    def test_rename_target_exists(self):
        """目标已存在。"""
        with tempfile.TemporaryDirectory() as parent:
            old_file = Path(parent) / "old.txt"
            old_file.write_text("old")
            existing = Path(parent) / "new.txt"
            existing.write_text("new")

            result = rename_file.invoke({
                "path": str(old_file),
                "new_name": "new.txt",
            })

            assert result.success is False
            assert result.error is not None
            assert "已存在" in result.error

    def test_rename_with_separator_in_name(self):
        """new_name 含路径分隔符时拒绝。"""
        with tempfile.TemporaryDirectory() as parent:
            old_file = Path(parent) / "old.txt"
            old_file.write_text("hello")

            result = rename_file.invoke({
                "path": str(old_file),
                "new_name": "sub\\new.txt",
            })

            assert result.success is False
            assert result.error is not None
            assert "路径分隔符" in result.error

    def test_result_fields(self):
        """结果字段完整性。"""
        with tempfile.TemporaryDirectory() as parent:
            old_file = Path(parent) / "old.txt"
            old_file.write_text("hello")

            result = rename_file.invoke({
                "path": str(old_file),
                "new_name": "new.txt",
            })

            assert isinstance(result, RenameResult)
            assert isinstance(result.old_path, str)
            assert isinstance(result.new_path, str)
            assert isinstance(result.success, bool)
