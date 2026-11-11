"""HumanConfirmMiddleware 测试。

测试多维度风险评估逻辑。
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.middleware.HumanConfirmMiddleware import (
    assess_risk,
    RISK_SAFE, RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL,
    _get_file_info,
    _check_system_dir,
    _build_summary,
)


class TestAssessRisk:
    """风险评估核心逻辑测试。"""

    def test_safe_tool_passes(self):
        """SAFE 工具直接放行，不看参数。"""
        risk, factors = assess_risk("search_files", {"query": "*"})
        assert risk == RISK_SAFE
        assert factors["base"] == "SAFE"

    def test_low_tool_base(self):
        """LOW 工具基础风险。"""
        risk, factors = assess_risk("copy_files", {"target_dir": "D:\\backup"})
        assert risk == RISK_LOW

    def test_high_tool_base(self):
        """HIGH 工具基础风险。"""
        risk, factors = assess_risk("delete_files", {"sources": []})
        assert risk >= RISK_MEDIUM  # HIGH 减去 small_op_discount 可能到 MEDIUM

    def test_medium_tool_base(self):
        """MEDIUM 工具基础风险。"""
        risk, factors = assess_risk("rename_file", {"path": "D:\\a.txt", "new_name": "b.txt"})
        assert risk >= RISK_MEDIUM

    def test_unknown_tool_defaults_safe(self):
        """未知工具默认 SAFE（只读操作不拦截）。"""
        risk, factors = assess_risk("unknown_tool", {})
        assert risk == RISK_SAFE

    def test_permanent_delete_boosts_risk(self):
        """永久删除风险提升。"""
        with tempfile.TemporaryDirectory() as d:
            # 创建 10 个文件
            files = []
            for i in range(10):
                f = Path(d) / f"f{i}.txt"
                f.write_text("x")
                files.append(str(f))

            risk_normal, _ = assess_risk("delete_files", {"sources": files, "to_recycle_bin": True})
            risk_permanent, factors = assess_risk("delete_files", {"sources": files, "to_recycle_bin": False})

            assert risk_permanent > risk_normal
            assert factors["is_permanent"] is True

    def test_many_files_boosts_risk(self):
        """文件数量多风险提升。"""
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i in range(60):
                f = Path(d) / f"f{i}.txt"
                f.write_text("x")
                files.append(str(f))

            risk, factors = assess_risk("delete_files", {"sources": files})
            assert factors.get("count_boost") is True

    def test_system_dir_boosts_risk(self):
        """系统目录风险提升。"""
        risk, factors = assess_risk("move_files", {"target_dir": "C:\\Windows\\Temp"})
        assert factors.get("is_system_dir") is True

    def test_single_file_discount(self):
        """单文件小操作风险降级。"""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "small.txt"
            f.write_text("hello")

            risk, factors = assess_risk("delete_files", {"sources": [str(f)]})
            assert factors.get("small_op_discount") is True


class TestGetFileInfo:
    """文件信息收集测试。"""

    def test_from_sources(self):
        """从 sources 参数获取文件信息。"""
        with tempfile.TemporaryDirectory() as d:
            f1 = Path(d) / "a.txt"
            f1.write_text("hello")
            f2 = Path(d) / "b.txt"
            f2.write_text("world!")

            count, size = _get_file_info({"sources": [str(f1), str(f2)]})
            assert count == 2
            assert size == 11  # 5 + 6

    def test_empty_sources(self):
        """空 sources 返回 0。"""
        count, size = _get_file_info({"sources": []})
        assert count == 0
        assert size == 0

    def test_no_params(self):
        """无参数返回 0。"""
        count, size = _get_file_info({})
        assert count == 0
        assert size == 0


class TestCheckSystemDir:
    """系统目录检测测试。"""

    def test_windows_dir(self):
        """C:\\Windows 是系统目录。"""
        assert _check_system_dir({"target_dir": "C:\\Windows\\Temp"}) is True

    def test_program_files(self):
        """C:\\Program Files 是系统目录。"""
        assert _check_system_dir({"path": "C:\\Program Files\\App"}) is True

    def test_user_dir(self):
        """用户目录不是系统目录。"""
        assert _check_system_dir({"target_dir": "D:\\Users\\test"}) is False


class TestBuildSummary:
    """摘要生成测试。"""

    def test_basic_summary(self):
        """基本摘要包含关键信息。"""
        factors = {
            "base": "HIGH",
            "file_count": 10,
            "total_size": 1024 * 1024,
            "is_permanent": False,
        }
        args = {"sources": ["C:\\Users\\file1.txt", "C:\\Users\\file2.txt"]}
        summary = _build_summary("delete_files", RISK_HIGH, factors, args)
        assert "删除" in summary
        assert "10" in summary
        assert "1.0 MB" in summary
        assert "file1.txt" in summary

    def test_permanent_delete_summary(self):
        """永久删除摘要包含不可恢复警告。"""
        factors = {
            "base": "HIGH",
            "file_count": 5,
            "total_size": 1024,
            "is_permanent": True,
            "permanent_boost": True,
        }
        args = {"sources": ["C:\\Users\\file1.txt"]}
        summary = _build_summary("delete_files", RISK_HIGH, factors, args)
        assert "不可恢复" in summary
        assert "永久删除" in summary
