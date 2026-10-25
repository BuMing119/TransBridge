"""Bounded background writes and ordered flush barriers for workflow logs."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import TextIO

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Barrier:
    kind: str
    done: threading.Event
    channel: str = ""


class WorkflowLogWriter:
    """Own files on one lazy worker; producers never hold the disk-I/O lock.

    Buffered text is charged at four bytes per character, an upper bound for
    UTF-8. Queue admission includes the batch currently being written. Flush and
    close barriers preserve per-channel order, including append after reopening.
    """

    def __init__(
        self,
        directory: Path,
        *,
        max_open_files: int,
        max_pending_bytes: int = 4 * 1024 * 1024,
        flush_bytes: int = 64 * 1024,
        flush_interval: float = 0.1,
    ) -> None:
        if min(max_open_files, max_pending_bytes, flush_bytes) < 1 or flush_interval <= 0:
            raise ValueError("log writer limits must be positive")
        if max_pending_bytes < 4:
            raise ValueError("log writer must have room for a UTF-8 character")
        self._directory = directory
        self._max_open_files = max_open_files
        self._max_pending_bytes = max_pending_bytes
        self._flush_bytes = min(flush_bytes, max_pending_bytes)
        self._flush_interval = flush_interval
        self._condition = threading.Condition()
        self._enqueue_lock = threading.Lock()
        self._queue: deque[tuple[str, str] | _Barrier] = deque()
        self._pending_bytes = 0
        self._closing = False
        self._finished = threading.Event()
        self._error = ""
        self._thread: threading.Thread | None = None
        self._handles: OrderedDict[str, TextIO] = OrderedDict()
        self._written: set[str] = set()

    @property
    def last_error(self) -> str:
        with self._condition:
            return self._error

    @property
    def pending_bytes(self) -> int:
        with self._condition:
            return self._pending_bytes

    def write(self, channel: str, text: str) -> None:
        # Serialize one write's fragments without keeping the queue lock while
        # waiting for space. The writer can always make progress independently.
        piece_size = min(16 * 1024, self._max_pending_bytes // 4)
        with self._enqueue_lock:
            for start in range(0, len(text), piece_size):
                piece = text[start : start + piece_size]
                size = len(piece) * 4
                with self._condition:
                    while (
                        not self._error and not self._closing and self._pending_bytes + size > self._max_pending_bytes
                    ):
                        self._condition.wait()
                    if self._error or self._closing:
                        return
                    self._queue.append((channel, piece))
                    self._pending_bytes += size
                    try:
                        self._start()
                    except (RuntimeError, OSError) as exc:
                        self._error = f"{type(exc).__name__}: {exc}"
                        self._queue.clear()
                        self._pending_bytes = 0
                        self._thread = None
                        self._finished.set()
                        self._condition.notify_all()
                        _logger.exception("无法启动 AI 工作流日志写入线程，日志已停用 (%s)", self._directory)
                        return
                    self._condition.notify_all()

    def flush(self) -> None:
        self._barrier("flush")

    def close_channel(self, channel: str) -> None:
        self._barrier("channel", channel)

    def close(self) -> None:
        with self._enqueue_lock, self._condition:
            if not self._closing:
                self._closing = True
                if self._thread is None:
                    self._finished.set()
                elif not self._error:
                    self._queue.append(_Barrier("finish", self._finished))
                    self._condition.notify_all()
            thread = self._thread
        self._finished.wait()
        if thread is not None:
            thread.join()

    def _barrier(self, kind: str, channel: str = "") -> None:
        done = threading.Event()
        with self._enqueue_lock, self._condition:
            if self._thread is None:
                return
            if self._closing or self._error:
                done = self._finished
            else:
                self._queue.append(_Barrier(kind, done, channel))
                self._condition.notify_all()
        done.wait()

    def _start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ai-log-writer", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        buffers: dict[str, list[str]] = {}
        buffered_bytes = 0
        deadline = time.monotonic() + self._flush_interval
        current: tuple[str, str] | _Barrier | None = None
        try:
            while True:
                with self._condition:
                    while not self._queue:
                        remaining = deadline - time.monotonic()
                        if buffers and remaining <= 0:
                            break
                        self._condition.wait(max(0, remaining) if buffers else None)
                    current = self._queue.popleft() if self._queue else None
                if isinstance(current, tuple):
                    channel, text = current
                    buffers.setdefault(channel, []).append(text)
                    buffered_bytes += len(text) * 4
                if isinstance(current, _Barrier) or buffered_bytes >= self._flush_bytes or time.monotonic() >= deadline:
                    self._flush(buffers, buffered_bytes)
                    buffers.clear()
                    buffered_bytes = 0
                    deadline = time.monotonic() + self._flush_interval
                if isinstance(current, _Barrier):
                    if current.kind == "channel":
                        handle = self._handles.pop(current.channel, None)
                        if handle is not None:
                            handle.close()
                    elif current.kind == "finish":
                        self._close_handles()
                        current.done.set()
                        return
                    current.done.set()
                current = None
        except Exception as exc:
            with self._condition:
                self._error = f"{type(exc).__name__}: {exc}"
            _logger.exception("AI 工作流日志写入失败，日志已停用 (%s)", self._directory)
        finally:
            self._close_handles()
            with self._condition:
                if isinstance(current, _Barrier):
                    current.done.set()
                for queued in self._queue:
                    if isinstance(queued, _Barrier):
                        queued.done.set()
                self._queue.clear()
                self._pending_bytes = 0
                self._condition.notify_all()
            self._finished.set()

    def _flush(self, buffers: dict[str, list[str]], size: int) -> None:
        for channel, parts in buffers.items():
            handle = self._handles.get(channel)
            if handle is None:
                if len(self._handles) >= self._max_open_files:
                    _, oldest = self._handles.popitem(last=False)
                    oldest.close()
                handle = (self._directory / f"{channel}.log").open(
                    "a" if channel in self._written else "w", encoding="utf-8"
                )
                self._handles[channel] = handle
                self._written.add(channel)
            self._handles.move_to_end(channel)
            handle.write("".join(parts))
            handle.flush()
        with self._condition:
            self._pending_bytes -= size
            self._condition.notify_all()

    def _close_handles(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except (OSError, ValueError) as exc:
                with self._condition:
                    self._error = self._error or f"{type(exc).__name__}: {exc}"
                _logger.warning("关闭 AI 工作流日志失败 (%s): %s", self._directory, exc)
        self._handles.clear()
