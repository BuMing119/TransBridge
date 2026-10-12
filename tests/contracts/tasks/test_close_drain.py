from __future__ import annotations

from dataclasses import replace
import threading

import pytest

from tests.contracts.test_task_runtime_backends import runtime, spec
from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.application.tasks import (
    CallbackThreadBackend,
    JobCapabilities,
    JobState,
    OwnerRef,
    ShutdownPolicy,
    TaskCleanupFailed,
    ThreadBackend,
)
from transbridge.ui.operations import OperationKind, OperationTaskAdapter, OperationTaskRequest

OWNER = OwnerRef("close-test", "gui")


def test_cancel_drains_rollback_and_blocks_late_commit_and_other_owner_admission():
    backend = ThreadBackend()
    tasks = runtime(backend)
    entered, release = threading.Event(), threading.Event()
    staging, official = ["candidate"], ["saved"]
    decisions = []
    ref = tasks.submit(spec(), OWNER).ref

    def workload(token):
        permit = tasks.commit_permit(ref, OWNER)
        entered.set()
        assert release.wait(3)
        try:
            decisions.append(tasks.try_commit(permit, lambda: official.extend(staging)).accepted)
            token.raise_if_cancelled()
        finally:
            staging.clear()

    tasks.schedule(ref, OWNER, workload)
    try:
        assert entered.wait(1)
        drain = tasks.prepare_close()
        drain.cancel()
        assert tasks.get(ref, OWNER).state is JobState.CANCELLING
        assert drain.poll().pending == (ref,)
        with pytest.raises(RuntimeError, match="suspended"):
            tasks.submit(spec(), OwnerRef("assistant", "assistant", session_id="other"))
        subscription = tasks.subscribe(lambda event: None)
        assert not subscription.closed
    finally:
        release.set()
        assert backend.join(ref.run_id, 2)
    assert not drain.poll().pending
    assert not drain.poll().errors
    assert tasks.get(ref, OWNER).state is JobState.CANCELLED
    assert decisions == [False] and not staging and official == ["saved"]
    drain.close()
    queued = tasks.submit(spec(), OWNER).ref
    tasks.cancel(queued, OWNER)
    subscription.close()
    tasks.shutdown(grace=1)


def test_terminal_commit_failure_does_not_hide_running_cleanup_or_its_error():
    backend = ThreadBackend()
    tasks = runtime(backend)
    failed, release = threading.Event(), threading.Event()
    ref = tasks.submit(spec(), OWNER).ref

    def workload(_token):
        try:
            tasks.try_commit(tasks.commit_permit(ref, OWNER), lambda: (_ for _ in ()).throw(OSError("publish")))
        except OSError:
            failed.set()
            assert release.wait(3)
            raise TaskCleanupFailed("rollback evidence retained") from None

    tasks.schedule(ref, OWNER, workload)
    try:
        assert failed.wait(1)
        assert tasks.get(ref, OWNER).state is JobState.FAILED
        drain = tasks.prepare_close()
        drain.cancel()
        assert drain.poll().pending == (ref,)
    finally:
        release.set()
        assert backend.join(ref.run_id, 2)
    status = drain.poll()
    assert not status.pending
    assert "rollback evidence retained" in status.errors[0]
    drain.close()
    # A second close attempt must still report unrepaired rollback failure.
    second = tasks.prepare_close()
    assert second.poll().errors == status.errors
    second.close()
    tasks.shutdown(grace=1)


def test_cancel_during_backend_dispatch_never_reports_quiet_before_start_finishes():
    entered, release = threading.Event(), threading.Event()

    class DelayedBackend(ThreadBackend):
        def start(self, run_id, target):
            entered.set()
            assert release.wait(3)
            super().start(run_id, target)

    backend = DelayedBackend()
    tasks = runtime(backend)
    ref = tasks.submit(spec(), OWNER).ref
    effects = []

    def workload(token):
        token.raise_if_cancelled()
        effects.append("published")

    dispatch = threading.Thread(target=lambda: tasks.schedule(ref, OWNER, workload))
    dispatch.start()
    try:
        assert entered.wait(1)
        drain = tasks.prepare_close()
        drain.cancel()
        assert tasks.get(ref, OWNER).state is JobState.CANCELLING
        assert drain.poll().pending == (ref,)
    finally:
        release.set()
        dispatch.join(2)
        assert not dispatch.is_alive()
        assert backend.join(ref.run_id, 2)
    assert not drain.poll().pending and not effects
    assert tasks.get(ref, OWNER).state is JobState.CANCELLED
    drain.close()
    tasks.shutdown(grace=1)


def test_close_cancels_queued_jobs_across_owner_scopes_and_holds_are_independent():
    tasks = runtime()
    refs = [tasks.submit(spec(), owner).ref for owner in (OWNER, OwnerRef("other", "assistant"))]
    first, second = tasks.prepare_close(), tasks.prepare_close()
    first.cancel()
    assert not first.poll().pending
    assert all(
        tasks.get(ref, owner).state is JobState.CANCELLED
        for ref, owner in zip(refs, (OWNER, OwnerRef("other", "assistant")), strict=True)
    )
    first.close()
    with pytest.raises(RuntimeError, match="suspended"):
        tasks.submit(spec(), OWNER)
    second.close()
    tasks.submit(spec(), OWNER)
    hold = tasks.prepare_close()
    tasks.shutdown(grace=1)
    hold.close()
    with pytest.raises(RuntimeError, match="closed"):
        tasks.submit(spec(), OWNER)


def test_unsupported_cancellation_keeps_close_incomplete_with_an_actionable_error():
    tasks = runtime()
    ref = tasks.submit(replace(spec(), capabilities=JobCapabilities(supports_cancel=False)), OWNER).ref
    tasks.start(ref, OWNER)
    drain = tasks.prepare_close()
    drain.cancel()
    status = drain.poll()
    assert status.pending == (ref,)
    assert "无法取消" in status.errors[0]
    assert tasks.get(ref, OWNER).state is JobState.RUNNING
    tasks.complete(ref, OWNER)
    drain.close()
    tasks.shutdown(grace=1, policy=ShutdownPolicy.WAIT)


@pytest.mark.parametrize("code", ["STAGING_CLEANUP_FAILED", "BUNDLE_STAGING_CLEANUP_FAILED", "BUNDLE_ROLLBACK_FAILED"])
@pytest.mark.parametrize("commit_failed", [False, True])
def test_operation_cleanup_diagnostic_survives_cancellation_and_blocks_close(code, commit_failed):
    tasks = runtime(CallbackThreadBackend(lambda _id, target: target()))
    adapter = OperationTaskAdapter(tasks)

    def workload(context):
        if commit_failed:
            try:
                tasks.try_commit(
                    tasks.commit_permit(context.ref, context.owner),
                    lambda: (_ for _ in ()).throw(OSError("failed atomic commit")),
                )
            except OSError:
                return OperationResult(
                    OperationOutcome.FAILED,
                    diagnostics=(Diagnostic(code, "backup retained"),),
                    counts=OperationCounts(failed=1),
                )
        tasks.cancel(context.ref, context.owner)
        return OperationResult.cancelled(Diagnostic(code, "backup retained"))

    ref = adapter.submit(OperationTaskRequest(OperationKind.WRITE, "a" * 64, "out", "write", workload, True), OWNER)
    assert tasks.get(ref, OWNER).state is JobState.FAILED
    assert adapter.result(ref, OWNER).diagnostics[0].code == code
    drain = tasks.prepare_close()
    assert code in drain.poll().errors[0]
    drain.close()
    tasks.shutdown(grace=1)


def test_final_shutdown_releases_custom_backend_after_reversible_drain():
    tasks = runtime()
    released = []
    backend = CallbackThreadBackend(
        lambda _id, target: target(), release=lambda _timeout: released.append(True) or True
    )
    ref = tasks.submit(spec(), OWNER).ref
    tasks.schedule(ref, OWNER, lambda _token: None, backend=backend)
    drain = tasks.prepare_close()
    drain.cancel()
    assert not drain.poll().pending
    assert not released
    result = tasks.shutdown(grace=1)
    assert result.backend_released and released == [True]
    assert result.requested == ()
    drain.close()
