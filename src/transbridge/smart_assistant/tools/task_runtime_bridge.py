"""Adapt legacy worker signals and commits to the single task runtime authority."""

from __future__ import annotations

from collections.abc import Callable
import threading

from transbridge.application.io.publish import CommitDecision, TaskRuntimeCommitGuard
from transbridge.application.tasks import (
    CallbackThreadBackend,
    JobState,
    TaskCancelled,
    TaskEventFilter,
    TransitionError,
)


class TaskRuntimeBridge:
    """One registered worker, with runtime-owned controls and terminal arbitration.

    Registration prepares the runtime backend; ``start`` launches the prepared work
    after the legacy caller has installed its closure and captured its input.
    """

    def __init__(self, runtime, ref, owner, handle, updated: Callable[[], None]) -> None:
        self.runtime = runtime
        self.ref = ref
        self.owner = owner
        self.handle = handle
        self._updated = updated
        self._scheduled: Callable[[], None] | None = None
        self._target: Callable[[], None] | None = None
        self._projection_lock = threading.Lock()
        self._projected_revision = -1
        self._subscription = runtime.subscribe(self._observe, event_filter=TaskEventFilter(run_id=ref.run_id))

    def schedule(self) -> None:
        backend = CallbackThreadBackend(self._prepare, wait=self.join, release=self._release)
        self.runtime.schedule(self.ref, self.owner, self._execute, backend=backend)

    def _prepare(self, _run_id: str, target: Callable[[], None]) -> None:
        self._scheduled = target

    def _observe(self, event) -> None:
        # Read current authority: concurrent publishers may deliver older events last.
        snapshot = self.runtime.get(self.ref, self.owner)
        with self._projection_lock:
            if snapshot.revision < self._projected_revision:
                return
            self._projected_revision = snapshot.revision
            self.handle.status = snapshot.state.value
            if self.handle.pause_event is None:
                self.handle.pause_event = threading.Event()
            if snapshot.state is JobState.PAUSED:
                self.handle.pause_event.clear()
            else:
                self.handle.pause_event.set()
            if snapshot.state in {JobState.CANCELLING, JobState.CANCELLED}:
                self.handle.stop_event.set()
            if event.message:
                self.handle.message = event.message
        self._updated()

    def wait_until_running(self) -> None:
        while True:
            snapshot = self.runtime.get(self.ref, self.owner)
            if self.handle.stop_event.is_set() or snapshot.state in {JobState.CANCELLING, JobState.CANCELLED}:
                raise TaskCancelled("任务已被用户停止")
            if snapshot.state is JobState.RUNNING:
                return
            if snapshot.is_terminal:
                raise RuntimeError(f"任务已结束: {snapshot.state.value}")
            self.handle.pause_event.wait(0.05)

    def commit(self, run_id: str, mutation: Callable[[], None]) -> CommitDecision:
        if run_id != self.ref.run_id:
            return CommitDecision(False, "run_id_mismatch")
        while True:
            try:
                self.wait_until_running()
                permit = self.runtime.commit_permit(self.ref, self.owner)
            except TaskCancelled:
                return CommitDecision(False, "cancelled")
            except TransitionError as exc:
                if exc.current is JobState.PAUSED:
                    continue
                return CommitDecision(False, exc.code)
            decision = TaskRuntimeCommitGuard(self.runtime, permit).commit(run_id, mutation)
            if decision.accepted:
                return decision
            # Progress or pause can invalidate a permit before the mutation begins.
            # Recheck authority and request a new one; never retry an executed mutation.
            if decision.reason not in {"revision_conflict", "terminal_or_inactive"}:
                return decision
            snapshot = self.runtime.get(self.ref, self.owner)
            if snapshot.is_terminal or snapshot.state is JobState.CANCELLING:
                return decision

    def _execute(self, token) -> None:
        try:
            self.wait_until_running()
            if self._target is not None:
                self._target()
        finally:
            if self.handle.stop_event.is_set() and not token.is_cancelled:
                snapshot = self.runtime.get(self.ref, self.owner)
                if not snapshot.is_terminal:
                    self.runtime.cancel(self.ref, self.owner)

    def start(self, target: Callable[[], None], finished: Callable[[], None]) -> threading.Thread:
        if self._scheduled is None or self.handle._thread is not None:
            raise RuntimeError("task worker is not prepared or was already started")
        self._target = target

        def run() -> None:
            try:
                self._scheduled()
            finally:
                finished()

        thread = threading.Thread(target=run, name=f"transbridge-{self.ref.run_id}", daemon=True)
        self.handle._thread = thread
        thread.start()
        return thread

    def join(self, _run_id: str, timeout: float | None = None) -> bool:
        thread = self.handle._thread
        if thread is None or thread.ident is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def _release(self, timeout: float | None = None) -> bool:
        released = self.join(self.ref.run_id, timeout)
        if released:
            self.close()
        return released

    def close(self) -> None:
        self._subscription.close()


def task_metadata(ctx, metadata: dict) -> dict:
    """Carry the captured caller scope into every assistant task identity."""
    context = getattr(ctx, "request_context", None) or getattr(ctx, "runtime_context", None)
    values = dict(metadata)
    for name in ("owner_id", "project_id", "variant_id", "session_id"):
        value = getattr(context, name, None) or getattr(ctx, name, None)
        if value:
            values[name] = str(getattr(value, "value", value))
    values["entrypoint"] = "smart-assistant"
    return values
