"""Nonblocking close attempt for runtime jobs and their rollback evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time

from transbridge.application.tasks.shutdown import TaskDrain
from transbridge.ui.workers import ApiWorker


@dataclass
class _CloseAttempt:
    aborted: threading.Event = field(default_factory=threading.Event)
    drain: TaskDrain | None = None
    ready: bool = False
    error: str | None = None


class TaskCloseGuard:
    def __init__(self, runtime, *, timeout: float = 5.0) -> None:
        self._runtime = runtime
        self._timeout = timeout
        self._attempt: _CloseAttempt | None = None
        self._worker: ApiWorker | None = None

    def begin(self) -> None:
        if self._attempt is not None:
            return
        attempt = self._attempt = _CloseAttempt()
        worker = self._worker = ApiWorker(self._cancel_and_wait, attempt, route_http_errors=False)
        worker.result.connect(lambda ready: self._finished(attempt, ready))
        worker.error.connect(lambda message: setattr(attempt, "error", message))
        worker.start()

    def _cancel_and_wait(self, attempt: _CloseAttempt) -> bool:
        # Admission/cancel share the commit lock, which may be held by an atomic
        # filesystem publish. Neither this lock nor backend joins belong on Qt.
        drain = attempt.drain = self._runtime.prepare_close()
        ready = False
        try:
            if attempt.aborted.is_set():
                return False
            drain.cancel()
            deadline = time.monotonic() + self._timeout
            while not attempt.aborted.is_set():
                status = drain.poll()
                if status.errors:
                    raise RuntimeError("后台任务取消或清理失败：\n" + "\n".join(status.errors))
                if not status.pending:
                    ready = True
                    return True
                if time.monotonic() >= deadline:
                    raise RuntimeError("后台任务尚未确认停止，已取消本次退出。请等待任务停止后重试。")
                attempt.aborted.wait(0.05)
            return False
        finally:
            if not ready or attempt.aborted.is_set():
                drain.close()

    @staticmethod
    def _finished(attempt: _CloseAttempt, ready: bool) -> None:
        if attempt.aborted.is_set():
            if attempt.drain is not None:
                attempt.drain.close()
        else:
            attempt.ready = ready

    def ready(self) -> bool:
        self.begin()
        if self._attempt.error:
            raise RuntimeError(self._attempt.error)
        return self._attempt.ready

    def abort(self) -> None:
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.aborted.set()
            if attempt.ready and attempt.drain is not None:
                # All jobs/backends are already drained, so no commit can hold
                # the lock. An in-flight attempt releases its own hold instead.
                attempt.drain.close()
