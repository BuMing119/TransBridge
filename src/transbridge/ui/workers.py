"""
ApiWorker: 通用后台工作线程。
所有 API 请求必须通过此类在后台线程执行，严禁在主线程直接调用 API。
"""

import re
import logging
import traceback
from typing import Callable
from PyQt6.QtCore import QThread, QObject, pyqtSignal

_logger = logging.getLogger(__name__)


# ── 全局 HTTP 错误信号总线 ──────────────────────────────────────────────────

class _HttpErrorBus(QObject):
    """
    模块级信号总线，用于将 401/403 HTTP 错误路由到 MainWindow 统一处理。

    Qt 信号跨线程安全（AutoConnection 机制），无需额外加锁。
    """
    http_error = pyqtSignal(int, str)   # (status_code, error_message)


_http_error_bus = _HttpErrorBus()


def get_http_error_bus() -> _HttpErrorBus:
    """返回全局 HTTP 错误信号总线单例，供 MainWindow 连接使用。"""
    return _http_error_bus


# ── 全局 API 状态信号总线 ──────────────────────────────────────────────────

class _ApiStatusBus(QObject):
    """
    模块级信号总线，追踪所有 ApiWorker 的请求生命周期。
    供 MainWindow 状态栏 API 状态指示器使用。

    Qt 信号跨线程安全（AutoConnection 机制），无需额外加锁。
    """
    request_started = pyqtSignal()
    request_finished = pyqtSignal(bool)  # True=成功, False=失败


_api_status_bus = _ApiStatusBus()


def get_api_status_bus() -> _ApiStatusBus:
    """返回全局 API 状态信号总线单例，供 MainWindow 连接使用。"""
    return _api_status_bus


def _parse_http_status(error_str: str) -> int | None:
    """从 'API Error 401: ...' 格式的错误字符串中提取 HTTP 状态码。"""
    m = re.match(r"API Error (\d{3}):", error_str)
    return int(m.group(1)) if m else None


# 由信号总线集中处理的状态码；这些错误不再 emit error 信号（防止标签页重复弹窗）
_GLOBAL_HANDLE_STATUSES = {401, 403}


# ── ApiWorker ──────────────────────────────────────────────────────────────

class ApiWorker(QThread):
    """
    在后台线程中执行任意可调用对象，通过信号将结果或错误返回主线程。

    用法::

        def _fetch():
            return api.list_projects()

        worker = ApiWorker(_fetch)
        worker.result.connect(self._on_projects_loaded)
        worker.error.connect(self._on_error)
        worker.finished.connect(lambda: self._set_loading(False))
        worker.start()
        self._workers.append(worker)   # 保留引用，防止 GC
    """

    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # current, total, message

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def make_progress_callback(self):
        """返回一个可在工作线程中调用的进度回调，安全地 emit progress 信号。"""
        def _cb(current: int, total: int, msg: str = ""):
            self.progress.emit(current, total, msg)
        return _cb

    def run(self) -> None:
        _api_status_bus.request_started.emit()
        try:
            self.result.emit(self._fn(*self._args, **self._kwargs))
            _api_status_bus.request_finished.emit(True)
        except Exception as exc:
            err_str = str(exc)
            # 记录完整错误到日志
            _logger.error("ApiWorker 捕获异常:\n%s", traceback.format_exc())
            status = _parse_http_status(err_str)
            if status in _GLOBAL_HANDLE_STATUSES:
                # 路由到全局信号总线，不再 emit error，防止标签页弹出重复 QMessageBox
                _http_error_bus.http_error.emit(status, err_str)
            else:
                self.error.emit(err_str)
            _api_status_bus.request_finished.emit(False)
