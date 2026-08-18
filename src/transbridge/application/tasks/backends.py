"""Framework-neutral execution backends.

Backends schedule and wait for callables. They never decide application task state.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
import threading
import time
from typing import Protocol, runtime_checkable

WorkItem = Callable[[], None]


@runtime_checkable
class TaskBackend(Protocol):
    def start(self, run_id: str, target: WorkItem) -> None: ...

    def cancel_hint(self, run_id: str) -> None: ...

    def join(self, run_id: str, timeout: float | None = None) -> bool: ...

    def close(self, timeout: float | None = None) -> bool: ...


class ThreadBackend:
    """One native thread per job, with bounded shutdown waiting."""

    def __init__(self, *, daemon: bool = False) -> None:
        self._daemon = daemon
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._closed = False

    def start(self, run_id: str, target: WorkItem) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("thread backend admission is closed")
            if run_id in self._threads:
                raise ValueError(f"run_id is already scheduled: {run_id}")
            thread = threading.Thread(target=target, name=f"transbridge-{run_id}", daemon=self._daemon)
            self._threads[run_id] = thread
            thread.start()

    def cancel_hint(self, run_id: str) -> None:
        del run_id  # native threads only support cooperative cancellation tokens

    def join(self, run_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def close(self, timeout: float | None = None) -> bool:
        with self._lock:
            self._closed = True
            run_ids = tuple(self._threads)
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        released = True
        for run_id in run_ids:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            released = self.join(run_id, remaining) and released
        return released


class BoundedThreadPoolBackend:
    """Thread pool capped at three concurrent workloads by contract."""

    def __init__(self, *, max_workers: int = 3, thread_name_prefix: str = "transbridge-task") -> None:
        if not 1 <= max_workers <= 3:
            raise ValueError("max_workers must be between 1 and 3")
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._lock = threading.RLock()
        self._futures: dict[str, Future[None]] = {}
        self._closed = False

    def start(self, run_id: str, target: WorkItem) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("thread-pool backend admission is closed")
            if run_id in self._futures:
                raise ValueError(f"run_id is already scheduled: {run_id}")
            self._futures[run_id] = self._executor.submit(target)

    def cancel_hint(self, run_id: str) -> None:
        with self._lock:
            future = self._futures.get(run_id)
        if future is not None:
            future.cancel()

    def join(self, run_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
        if future is None:
            return True
        try:
            future.result(timeout=timeout)
        except FutureTimeout:
            return False
        except BaseException:  # workload wrapper owns state and diagnostics
            return True
        return True

    def close(self, timeout: float | None = None) -> bool:
        with self._lock:
            self._closed = True
            run_ids = tuple(self._futures)
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        released = True
        for run_id in run_ids:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            released = self.join(run_id, remaining) and released
        self._executor.shutdown(wait=released, cancel_futures=False)
        return released


@dataclass(slots=True)
class CallbackThreadBackend:
    """QThread/signal-friendly wrapper without importing or inheriting Qt types."""

    dispatch: Callable[[str, WorkItem], None]
    cancel: Callable[[str], None] = lambda _run_id: None
    wait: Callable[[str, float | None], bool] = lambda _run_id, _timeout: True
    release: Callable[[float | None], bool] = lambda _timeout: True

    def start(self, run_id: str, target: WorkItem) -> None:
        self.dispatch(run_id, target)

    def cancel_hint(self, run_id: str) -> None:
        self.cancel(run_id)

    def join(self, run_id: str, timeout: float | None = None) -> bool:
        return self.wait(run_id, timeout)

    def close(self, timeout: float | None = None) -> bool:
        return self.release(timeout)
