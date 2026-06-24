"""Everything SDK 客户端封装。

通过 ctypes 调用 Everything64.dll，提供毫秒级全盘文件搜索能力。
Everything 通过直接读取 NTFS MFT 构建内存索引，比 os.walk 快 100-1000 倍。

注意：Everything SDK 使用全局状态，非线程安全。
所有 SDK 调用通过 _EVERYTHING_LOCK 串行化。
"""
import ctypes
import logging
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

from backend.models import SearchResult
from backend.utils.path_finder import find_file

logger = logging.getLogger(__name__)

# Everything SDK 全局锁（SDK 使用全局状态，非线程安全）
_EVERYTHING_LOCK = threading.Lock()

from backend.utils.path_finder import find_file
from backend.models import SearchResult

logger = logging.getLogger(__name__)

# Everything SDK 请求标志（位掩码，用 | 组合）
EVERYTHING_REQUEST_FILE_NAME = 0x00000001
EVERYTHING_REQUEST_PATH = 0x00000002
EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME = 0x00000004
EVERYTHING_REQUEST_SIZE = 0x00000010
EVERYTHING_REQUEST_DATE_MODIFIED = 0x00000020


class EverythingClient:
    """Everything SDK 客户端。

    封装 Everything64.dll 的 ctypes 调用，提供文件搜索和元数据查询功能。
    自动检测 Everything 桌面应用是否运行，未运行时自动启动。
    """

    def __init__(self):
        dll_path = find_file(
            "Everything64.dll",
            env_var="EVERYTHING_SDK_PATH",
        )
        if not dll_path:
            raise FileNotFoundError("找不到 Everything64.dll，请安装 Everything SDK")
        self.dll = ctypes.WinDLL(str(dll_path))
        self._setup_functions()

    def _setup_functions(self):
        """设置 DLL 函数的参数类型和返回值类型。"""
        dll = self.dll

        # void Everything_SetSearchW(LPCWSTR query)
        dll.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
        dll.Everything_SetSearchW.restype = None

        # void Everything_SetMax(DWORD max)
        dll.Everything_SetMax.argtypes = [wintypes.DWORD]
        dll.Everything_SetMax.restype = None

        # void Everything_SetRequestFlags(DWORD flags)
        dll.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
        dll.Everything_SetRequestFlags.restype = None

        # BOOL Everything_QueryW(BOOL wait)
        dll.Everything_QueryW.argtypes = [wintypes.BOOL]
        dll.Everything_QueryW.restype = wintypes.BOOL

        # DWORD Everything_GetNumResults(void)
        dll.Everything_GetNumResults.argtypes = []
        dll.Everything_GetNumResults.restype = wintypes.DWORD

        # DWORD Everything_GetResultFullPathNameW(DWORD, LPWSTR, DWORD)
        dll.Everything_GetResultFullPathNameW.argtypes = [
            wintypes.DWORD,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        dll.Everything_GetResultFullPathNameW.restype = wintypes.DWORD

        # BOOL Everything_GetResultSize(DWORD, LARGE_INTEGER*)
        dll.Everything_GetResultSize.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_longlong),
        ]
        dll.Everything_GetResultSize.restype = wintypes.BOOL

        # BOOL Everything_GetResultDateModified(DWORD, FILETIME*)
        dll.Everything_GetResultDateModified.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.FILETIME),
        ]
        dll.Everything_GetResultDateModified.restype = wintypes.BOOL

        # DWORD Everything_GetLastError(void)
        dll.Everything_GetLastError.argtypes = []
        dll.Everything_GetLastError.restype = wintypes.DWORD

        # void Everything_Reset(void)
        dll.Everything_Reset.argtypes = []
        dll.Everything_Reset.restype = None

    def search(self, query: str, max_results: int = 500) -> list[SearchResult]:
        """执行文件搜索。

        Args:
            query: Everything 查询语法字符串。支持通配符、path:、ext:、size:、dm: 等过滤器。
            max_results: 最大返回结果数。

        Returns:
            搜索结果列表，包含文件名、路径、大小、修改时间。
        """
        with _EVERYTHING_LOCK:
            return self._search_locked(query, max_results)

    def _search_locked(self, query: str, max_results: int) -> list[SearchResult]:
        """内部搜索实现（必须在 _EVERYTHING_LOCK 内调用）。"""
        dll = self.dll

        # 重置状态 → 设置查询 → 执行
        dll.Everything_Reset()
        dll.Everything_SetSearchW(query)
        dll.Everything_SetMax(max_results)

        flags = (
            EVERYTHING_REQUEST_FILE_NAME
            | EVERYTHING_REQUEST_PATH
            | EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME
            | EVERYTHING_REQUEST_SIZE
            | EVERYTHING_REQUEST_DATE_MODIFIED
        )
        dll.Everything_SetRequestFlags(flags)

        t0 = time.time()
        if not dll.Everything_QueryW(True):
            logger.warning("Everything 查询失败: query=%s, error=%d", query, dll.Everything_GetLastError())
            return []

        num = dll.Everything_GetNumResults()
        logger.info("Everything 查询完成: query=%s, num=%d, %.1fs", query, num, time.time() - t0)
        if num == 0:
            return []

        # 6. 循环获取每个结果的详细信息
        results = []
        path_buf = ctypes.create_unicode_buffer(520)  # MAX_PATH 足够长

        for i in range(min(num, max_results)):
            # 获取完整路径
            dll.Everything_GetResultFullPathNameW(i, path_buf, 520)
            full_path = path_buf.value

            # 获取文件大小
            size = ctypes.c_longlong()
            dll.Everything_GetResultSize(i, ctypes.byref(size))

            # 跳过零字节的 # 文件（Everything 索引垃圾数据）
            if size.value == 0 and Path(full_path).name == "#":
                continue

            # 获取修改时间
            filetime = wintypes.FILETIME()
            dll.Everything_GetResultDateModified(i, ctypes.byref(filetime))
            modified_str = self._filetime_to_str(filetime)

            p = Path(full_path)
            results.append(SearchResult(
                name=p.name,
                path=str(p.parent),
                full_path=full_path,
                size=size.value,
                modified=modified_str,
            ))

        return results

    def is_available(self) -> bool:
        """检查 Everything 是否在运行，未运行时自动启动。"""
        if self._check_running():
            return True

        logger.info("Everything 未运行，尝试自动启动...")
        if not self._start_everything():
            return False

        # 等待 Everything 就绪（索引构建需要时间）
        for _ in range(30):
            time.sleep(1)
            if self._check_running():
                logger.info("Everything 已启动并就绪")
                return True

        logger.warning("Everything 启动超时")
        return False

    def _check_running(self) -> bool:
        """检测 Everything 服务是否可响应查询。"""
        try:
            with _EVERYTHING_LOCK:
                self.dll.Everything_Reset()
                self.dll.Everything_SetSearchW("")
                self.dll.Everything_SetMax(1)
                return bool(self.dll.Everything_QueryW(True))
        except Exception:
            return False

    @staticmethod
    def _start_everything() -> bool:
        """查找并启动 Everything.exe。"""
        exe_path = find_file(
            "Everything.exe",
            env_var="EVERYTHING_SDK_PATH",
        )
        if not exe_path:
            logger.warning("找不到 Everything.exe，请安装 Everything")
            return False

        try:
            # -startup 后台启动，不弹窗口
            subprocess.Popen(
                [str(exe_path), "-startup"],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            logger.info("已启动 Everything: %s", exe_path)
            return True
        except Exception as e:
            logger.warning("启动 Everything 失败: %s", e)
            return False

    @staticmethod
    def _filetime_to_str(ft: wintypes.FILETIME) -> str:
        """将 Windows FILETIME 转为可读的日期字符串。"""
        if ft.dwLowDateTime == 0 and ft.dwHighDateTime == 0:
            return ""
        timestamp = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        import datetime
        epoch = datetime.datetime(1601, 1, 1)
        dt = epoch + datetime.timedelta(microseconds=timestamp // 10)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
