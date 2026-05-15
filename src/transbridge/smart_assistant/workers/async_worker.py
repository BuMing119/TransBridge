"""AsyncWorker — LLM/Agent 后台任务的 threading.Thread 基类。

Phase 2: 替代 QThread 继承。通过回调属性实现信号功能，
调用方通过 QTimer.singleShot 将回调桥接到主线程以保证 Qt GUI 安全。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Callable


class AsyncWorker(threading.Thread, ABC):
    """纯 Python 后台工作线程基类。

    回调属性（由调用方赋值）:
        on_chunk:     流式文本到达回调
        on_finished:  任务成功完成回调
        on_error:     任务失败回调
        on_token_usage: Token 统计回调 (model, input_tokens, output_tokens)

    取消机制:
        cancel() 设置 _cancelled 事件，子类在 run() 中检查 is_cancelled()
    """

    def __init__(self, daemon: bool = True):
        super().__init__(daemon=daemon)
        self._cancelled = threading.Event()
        self.on_chunk: Callable[[str], None] | None = None
        self.on_finished: Callable[[str], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_token_usage: Callable[[str, int, int], None] | None = None

    @abstractmethod
    def run(self) -> None:
        """子类必须实现：在此执行后台任务，并调用 on_chunk/on_finished/on_error 回调。"""

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()
