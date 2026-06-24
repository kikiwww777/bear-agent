"""move_files 工具测试。

使用 Handle 模式：search_files 返回句柄，move_files 通过句柄获取结果。
"""
import tempfile
from pathlib import Path

import pytest

from backend.models import FileOperationResult
from backend.tools.search_files import search_files
from backend.tools.move_files import move_files


# ============================================================
#  move_files 工具测试（Handle 模式）
# ============================================================

class TestMoveFiles:
    """move_files 工具集成测试（Handle 模式）。"""

    def test_invalid_result_id(self):
        """无效的 result_id。"""
        result = move_files.invoke({
            "result_id": "invalid_id",
            "target_dir": "C:\\temp\\backup",
        })
        assert result.total_files == 0
        assert result.success_count == 0
        assert result.fail_count == 0

    def test_no_params(self):
        """不传 result_id 和 sources。"""
        with tempfile.TemporaryDirectory() as target_dir:
            result = move_files.invoke({
                "target_dir": target_dir,
            })
            assert result.total_files == 0
            assert result.success_count == 0

    def test_result_fields(self):
        """结果字段完整性。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建测试文件
                test_file = Path(source_dir) / "test.txt"
                test_file.write_text("hello")

                result = move_files.invoke({
                    "sources": [str(test_file)],
                    "target_dir": target_dir,
                })

                # 检查结果字段
                assert isinstance(result, FileOperationResult)
                assert isinstance(result.total_files, int)
                assert isinstance(result.total_size, int)
                assert isinstance(result.success_count, int)
                assert isinstance(result.fail_count, int)
                assert isinstance(result.failed_files, list)


# ============================================================
#  move_files 工具测试（sources 模式）
# ============================================================

class TestMoveFilesWithSources:
    """move_files 工具测试（sources 模式）。"""

    def test_move_single_file(self):
        """移动单个文件。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建测试文件
                test_file = Path(source_dir) / "test.txt"
                test_file.write_text("hello")

                # 直接传路径列表
                result = move_files.invoke({
                    "sources": [str(test_file)],
                    "target_dir": target_dir,
                })

                assert result.total_files == 1
                assert result.success_count == 1
                assert result.fail_count == 0

                # 检查文件是否移动成功
                target_file = Path(target_dir) / "test.txt"
                assert target_file.exists()
                assert target_file.read_text() == "hello"

                # 源文件应该消失
                assert not test_file.exists()

    def test_move_multiple_files(self):
        """移动多个文件。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建多个测试文件
                files = []
                for i in range(5):
                    test_file = Path(source_dir) / f"test_{i}.txt"
                    test_file.write_text(f"content {i}")
                    files.append(str(test_file))

                # 直接传路径列表
                result = move_files.invoke({
                    "sources": files,
                    "target_dir": target_dir,
                })

                assert result.total_files == 5
                assert result.success_count == 5
                assert result.fail_count == 0

    def test_move_nonexistent_file(self):
        """移动不存在的文件。"""
        with tempfile.TemporaryDirectory() as target_dir:
            result = move_files.invoke({
                "sources": ["C:\\nonexistent\\file.txt"],
                "target_dir": target_dir,
            })

            # 不存在的文件会被跳过
            assert result.total_files == 0
            assert result.success_count == 0

    def test_no_params(self):
        """不传参数。"""
        with tempfile.TemporaryDirectory() as target_dir:
            result = move_files.invoke({
                "target_dir": target_dir,
            })

            # 必须传 result_id 或 sources
            assert result.total_files == 0
            assert result.success_count == 0

    def test_move_preserves_content(self):
        """移动后文件内容不变。"""
        with tempfile.TemporaryDirectory() as source_dir:
            with tempfile.TemporaryDirectory() as target_dir:
                # 创建带内容的文件
                test_file = Path(source_dir) / "data.txt"
                content = "这是一段测试内容\n第二行\n第三行"
                test_file.write_text(content, encoding="utf-8")

                # 移动
                result = move_files.invoke({
                    "sources": [str(test_file)],
                    "target_dir": target_dir,
                })

                assert result.success_count == 1

                # 验证内容
                target_file = Path(target_dir) / "data.txt"
                assert target_file.read_text(encoding="utf-8") == content
