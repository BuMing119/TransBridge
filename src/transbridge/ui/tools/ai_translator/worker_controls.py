"""Order runtime snapshots and worker lifetime without holding a runtime lock."""

from collections.abc import Callable
import threading

from transbridge.application.tasks import JobSnapshot, JobState


class SerialAiWorkerControls:
    """Serialize acceptance and synchronous, thread-safe worker control methods.

    Callers must obtain snapshots before entering this boundary and must not hold
    adapter/runtime locks. The lock also fences unbind/close against an in-flight
    action. Reentrant signal callbacks enqueue the latest state until the current
    worker method returns instead of recursively taking its non-reentrant lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: JobSnapshot | None = None
        self._worker: object | None = None
        self._cancel_run: Callable[[], None] | None = None
        self._closed = False
        self._pending = False
        self._draining = False
        self._last_control = None

    def accept(self, snapshot: JobSnapshot) -> None:
        with self._lock:
            if self._closed or not self._newer(snapshot):
                return
            self._drain()

    def bind_worker(self, worker: object, snapshot: JobSnapshot) -> None:
        with self._lock:
            if self._closed:
                return
            self._worker = worker
            self._last_control = None
            self._newer(snapshot)
            self._pending = True
            self._drain()

    def unbind_worker(self, worker: object) -> None:
        with self._lock:
            if self._worker is worker:
                self._worker = None
                self._last_control = None

    def bind_run(self, callback: Callable[[], None] | None, snapshot: JobSnapshot) -> None:
        with self._lock:
            if self._closed:
                return
            self._cancel_run = callback
            self._newer(snapshot)
            self._pending = True
            self._drain()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._worker = None
            self._cancel_run = None
            self._pending = False

    def _newer(self, snapshot: JobSnapshot) -> bool:
        if self._snapshot is not None and snapshot.sequence <= self._snapshot.sequence:
            return False
        self._snapshot = snapshot
        self._pending = True
        return True

    def _drain(self) -> None:
        if self._draining:
            return
        self._draining = True
        try:
            while self._pending and not self._closed:
                self._pending = False
                snapshot = self._snapshot
                if snapshot is None:
                    continue
                state = snapshot.state
                if state in {JobState.CANCELLING, JobState.CANCELLED} and self._cancel_run is not None:
                    self._cancel_run()
                # A synchronous notification may close/rebind the run or publish
                # newer authority. Drain that state before invoking any worker.
                if self._closed or self._pending:
                    continue
                worker = self._worker
                action = {
                    JobState.PAUSED: "pause",
                    JobState.RUNNING: "resume",
                    JobState.CANCELLING: "stop",
                    JobState.CANCELLED: "stop",
                }.get(state)
                if worker is None or action is None:
                    continue
                if self._last_control == (worker, action):
                    continue
                callback = getattr(worker, action, None)
                if not callable(callback) and action == "stop":
                    callback = getattr(worker, "cancel", None)
                if callable(callback):
                    self._last_control = (worker, action)
                    callback()
        finally:
            self._draining = False
