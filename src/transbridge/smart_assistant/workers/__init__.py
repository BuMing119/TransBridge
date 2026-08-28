"""后台工作线程基类 — 替代 QThread 的纯 Python 实现。"""

from .async_worker import AsyncWorker

__all__ = ["AsyncWorker"]
