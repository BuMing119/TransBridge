"""Real task backends preserve the winning commit and finish queued cancellation."""

from threading import Event, Thread

import pytest

from tests.ui.operations.test_production_facade import _Clock, _Ids
from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import (
    BoundedThreadPoolBackend,
    JobState,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
    ThreadBackend,
)
from transbridge.ui.operations import batch_download_commit as module
from transbridge.ui.operations.batch_download_view import DownloadReceipt


def task_setup(backend):
    runtime = TaskRuntime(id_generator=_Ids(), clock=_Clock(), backend=backend)
    context = RequestContext("batch-download-task", project_id="project", variant_id="variant")
    actor = OwnerRef(context.owner_id, "gui.operation-plan", context.project_id, context.variant_id)
    return runtime, context, actor


@pytest.fixture
def real_task():
    backend = ThreadBackend(daemon=True)
    runtime, context, actor = task_setup(backend)
    yield runtime, context, actor, backend
    runtime.shutdown(grace=1)


def test_commit_wins_late_cancel_and_returns_real_success_with_completed_task(real_task, monkeypatch):
    runtime, context, actor, backend = real_task
    cancel, observed_terminal = Event(), Event()
    applied = []
    original_get = runtime.get

    def get(ref, owner):
        snapshot = original_get(ref, owner)
        if snapshot.is_terminal:
            observed_terminal.set()
        return snapshot

    monkeypatch.setattr(runtime, "get", get)

    def operation(task_context, commit, _cancellation):
        commit(lambda: applied.append(task_context.run_id))
        cancel.set()
        assert observed_terminal.wait(1), "Caller must observe committed completion while the worker is still active"
        return DownloadReceipt(1)

    receipt = module.run_download_task(runtime, context, "late-cancel", operation, cancel)
    task = runtime.list(actor)[0]
    assert backend.join(task.ref.run_id, 1)
    assert receipt.updated == 1 and applied == [task.ref.run_id]
    assert runtime.get(task.ref, actor).state is JobState.COMPLETED


def test_cancel_before_commit_prevents_mutation_and_finishes_cancelled(real_task):
    runtime, context, actor, backend = real_task
    cancel = Event()
    applied = []

    def operation(_context, commit, _cancellation):
        cancel.set()
        commit(lambda: applied.append("must not publish"))
        return DownloadReceipt(1)

    with pytest.raises(TaskCancelled):
        module.run_download_task(runtime, context, "before-commit-cancel", operation, cancel)

    task = runtime.list(actor)[0]
    assert backend.join(task.ref.run_id, 1)
    assert applied == []
    assert runtime.get(task.ref, actor).state is JobState.CANCELLED


def test_completion_between_poll_and_cancel_is_a_normal_terminal_race(real_task, monkeypatch):
    runtime, context, actor, backend = real_task
    cancel, release = Event(), Event()
    original_cancel = runtime.cancel
    races = []

    def cancel_after_workload_finishes(ref, owner):
        races.append(ref)
        release.set()
        assert backend.join(ref.run_id, 1)
        return original_cancel(ref, owner)

    monkeypatch.setattr(runtime, "cancel", cancel_after_workload_finishes)

    def operation(_context, _commit, _cancellation):
        cancel.set()
        assert release.wait(1)
        return DownloadReceipt(0)

    receipt = module.run_download_task(runtime, context, "poll-race", operation, cancel)
    task = runtime.list(actor)[0]
    assert receipt.updated == 0 and races == [task.ref]
    assert runtime.get(task.ref, actor).state is JobState.COMPLETED


def test_workload_error_keeps_original_cause_and_failed_task(real_task):
    runtime, context, actor, backend = real_task
    failure = OSError("磁盘写入失败，具体故障仍需保留")

    def operation(_context, _commit, _cancellation):
        raise failure

    with pytest.raises(OSError) as caught:
        module.run_download_task(runtime, context, "write-error", operation, Event())

    task = runtime.list(actor)[0]
    assert backend.join(task.ref.run_id, 1)
    assert caught.value is failure
    assert runtime.get(task.ref, actor).state is JobState.FAILED


def test_cancelling_a_queued_pool_workload_returns_without_waiting_for_its_body(monkeypatch):
    backend = BoundedThreadPoolBackend(max_workers=1)
    runtime, context, actor = task_setup(backend)
    occupied, release_blocker, caller_finished, abort_cleanup = Event(), Event(), Event(), Event()
    cancel, cancellation_forwarded = Event(), Event()
    outcome, ran = {}, []

    class AbortableWait(Event):
        """Ensure a regression fails the test without leaking a stuck test thread."""

        def wait(self, timeout=None):
            if abort_cleanup.is_set():
                raise RuntimeError("Test cleanup interrupted the stuck caller")
            return super().wait(timeout)

    monkeypatch.setattr(module, "Event", AbortableWait)
    original_cancel = runtime.cancel

    def record_cancel(ref, owner):
        result = original_cancel(ref, owner)
        cancellation_forwarded.set()
        return result

    monkeypatch.setattr(runtime, "cancel", record_cancel)

    def blocker():
        occupied.set()
        release_blocker.wait(3)

    def operation(_context, _commit, _cancellation):
        ran.append("must never run")
        return DownloadReceipt(1)

    def caller():
        try:
            outcome["receipt"] = module.run_download_task(runtime, context, "queued-cancel", operation, cancel)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            caller_finished.set()

    backend.start("occupied-test-worker", blocker)
    thread = Thread(target=caller, daemon=True)
    try:
        assert occupied.wait(1)
        cancel.set()
        thread.start()
        assert cancellation_forwarded.wait(1)
        assert caller_finished.wait(1), "Cancelled queued Future has no execute/finally to signal the caller"
        assert isinstance(outcome.get("error"), TaskCancelled)
        assert "receipt" not in outcome and ran == []
        assert runtime.list(actor)[0].state is JobState.CANCELLED
    finally:
        abort_cleanup.set()
        release_blocker.set()
        if thread.ident is not None:
            thread.join(1)
        runtime.shutdown(grace=1)
