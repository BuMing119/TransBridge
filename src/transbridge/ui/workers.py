"""
ApiWorker: 通用后台工作线程。
所有 API 请求必须通过此类在后台线程执行，严禁在主线程直接调用 API。
"""

from typing import Callable
from PyQt6.QtCore import QThread, pyqtSignal


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
        try:
            self.result.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:
            self.error.emit(str(exc))
