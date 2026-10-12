from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog
import pytest

from tests.contracts.test_task_runtime_backends import runtime, spec
from tests.ui import test_ai_task_shutdown
from transbridge.application.tasks import JobState, OwnerRef, TaskCleanupFailed, ThreadBackend
from transbridge.ui.shell.window_lifecycle import WindowLifecycle
from transbridge.ui.worker_registry import get_api_worker_registry

qapp = test_ai_task_shutdown.qapp
shutdown_host = test_ai_task_shutdown.shutdown_host
OWNER = OwnerRef("operation-owner", "gui.operation-plan")


@pytest.fixture
def runtime_close(shutdown_host, qapp):
    host, registry, _old_lifecycle, scheduled, warning = shutdown_host
    backend = ThreadBackend()
    tasks = runtime(backend)
    host.app_runtime = SimpleNamespace(tasks=tasks)
    lifecycle = WindowLifecycle(host)
    yield host, lifecycle, tasks, backend, scheduled, warning
    lifecycle.auto_saver.stop()
    lifecycle._input_guard.resume()
    lifecycle._task_close.abort()
    worker = lifecycle._task_close._worker
    if worker is not None:
        assert worker.wait(2000)
        _wait(qapp, lambda: not get_api_worker_registry().busy)
    tasks.shutdown(grace=1)


def _wait(qapp, condition):
    deadline = time.monotonic() + 2
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(10)
        qapp.processEvents()
    assert condition()


def _probe_finished(qapp, lifecycle):
    _wait(qapp, lambda: lifecycle._task_close._attempt.ready or lifecycle._task_close._attempt.error)
    _wait(qapp, lambda: not get_api_worker_registry().busy)


def test_close_cancels_download_rolls_back_candidate_and_saves_only_committed_results(runtime_close, tmp_path, qapp):
    host, lifecycle, tasks, backend, scheduled, _warning = runtime_close
    entered, release = threading.Event(), threading.Event()
    official, candidate = ["previous"], ["uncommitted"]
    saved = tmp_path / "project.json"
    ref = tasks.submit(spec(), OWNER).ref

    def workload(token):
        assert tasks.try_commit(tasks.commit_permit(ref, OWNER), lambda: official.append("committed")).accepted
        permit = tasks.commit_permit(ref, OWNER)
        entered.set()
        assert release.wait(3)
        try:
            assert not tasks.try_commit(permit, lambda: official.extend(candidate)).accepted
            token.raise_if_cancelled()
        finally:
            candidate.clear()

    def save(*, on_finished):
        assert not candidate
        saved.write_text(json.dumps(official), encoding="utf-8")
        on_finished(True)
        return True

    host.save_current_project_async.side_effect = save
    tasks.schedule(ref, OWNER, workload)
    try:
        assert entered.wait(1)
        lifecycle.close_event(QCloseEvent())
        _wait(qapp, lambda: tasks.cancellation_token(ref, OWNER).is_cancelled)
        assert tasks.get(ref, OWNER).state is JobState.CANCELLING
        host.save_current_project_async.assert_not_called()
        host.context.close_projection.assert_not_called()
    finally:
        release.set()
        assert backend.join(ref.run_id, 2)
    _probe_finished(qapp, lifecycle)
    scheduled.pop()[1]()
    host.close.assert_called_once()
    host.context.close_projection.assert_called_once()
    assert json.loads(saved.read_text(encoding="utf-8")) == ["previous", "committed"]
    with pytest.raises(RuntimeError, match="suspended"):
        tasks.submit(spec(), OWNER)


def test_save_failure_reopens_admission_without_restarting_cancelled_jobs(runtime_close, qapp):
    host, lifecycle, tasks, _backend, scheduled, warning = runtime_close
    old = tasks.submit(spec(), OWNER).ref
    lifecycle.close_event(QCloseEvent())
    _probe_finished(qapp, lifecycle)
    scheduled.pop()[1]()
    with pytest.raises(RuntimeError, match="suspended"):
        tasks.submit(spec(), OWNER)
    host.save_current_project_async.call_args.kwargs["on_finished"](False)
    assert not host.close_pending and not host.close_ready
    host.close.assert_not_called()
    warning.assert_called_once()
    assert tasks.get(old, OWNER).state is JobState.CANCELLED
    new = tasks.submit(spec(), OWNER).ref
    assert tasks.get(new, OWNER).state is JobState.QUEUED


@pytest.mark.parametrize("cleanup_failed", [False, True], ids=["timeout", "rollback-failure"])
def test_unfinished_or_failed_rollback_keeps_window_and_projection_open(runtime_close, qapp, cleanup_failed):
    host, lifecycle, tasks, backend, scheduled, warning = runtime_close
    entered, release = threading.Event(), threading.Event()
    if not cleanup_failed:
        lifecycle._task_close._timeout = 0
    ref = tasks.submit(spec(), OWNER).ref

    def workload(token):
        entered.set()
        assert release.wait(3)
        if cleanup_failed:
            raise TaskCleanupFailed("rollback failed; backup retained")
        token.raise_if_cancelled()

    tasks.schedule(ref, OWNER, workload)
    try:
        assert entered.wait(1)
        lifecycle.close_event(QCloseEvent())
        _wait(qapp, lambda: tasks.cancellation_token(ref, OWNER).is_cancelled)
        if cleanup_failed:
            release.set()
            assert backend.join(ref.run_id, 2)
        _probe_finished(qapp, lifecycle)
        scheduled.pop()[1]()
        assert not host.close_pending
        host.save_current_project_async.assert_not_called()
        host.context.close_projection.assert_not_called()
        host.close.assert_not_called()
        warning.assert_called_once()
        assert ("rollback failed" if cleanup_failed else "尚未确认停止") in warning.call_args.args[2]
        tasks.submit(spec(), OWNER)
    finally:
        release.set()
        assert backend.join(ref.run_id, 2)


def test_runtime_cancellation_is_requested_while_foreground_worker_is_still_running(runtime_close, qapp):
    host, lifecycle, tasks, _backend, _scheduled, _warning = runtime_close
    ref = tasks.submit(spec(), OWNER).ref
    host.foreground_worker = Mock()
    host.foreground_worker.isRunning.return_value = True
    lifecycle.close_event(QCloseEvent())
    _probe_finished(qapp, lifecycle)
    assert tasks.get(ref, OWNER).state is JobState.CANCELLED
    host.save_current_project_async.assert_not_called()


def test_successful_close_also_closes_parentless_operation_dialog(runtime_close, qapp):
    host, lifecycle, _tasks, _backend, scheduled, _warning = runtime_close
    dialog = QDialog()
    dialog.show()
    qapp.processEvents()
    try:
        lifecycle.close_event(QCloseEvent())
        assert dialog.isVisible() and not dialog.isEnabled()
        _probe_finished(qapp, lifecycle)
        scheduled.pop()[1]()
        host.save_current_project_async.call_args.kwargs["on_finished"](True)
        assert not dialog.isVisible()
        host.close.assert_called_once()
    finally:
        dialog.close()


def test_close_never_waits_for_an_inflight_commit_lock_on_the_qt_thread(runtime_close, qapp):
    host, lifecycle, tasks, backend, scheduled, _warning = runtime_close
    entered, release = threading.Event(), threading.Event()
    ref = tasks.submit(spec(), OWNER).ref

    def mutation():
        entered.set()
        assert release.wait(3)

    tasks.schedule(ref, OWNER, lambda _token: tasks.try_commit(tasks.commit_permit(ref, OWNER), mutation))
    try:
        assert entered.wait(1)
        started = time.monotonic()
        lifecycle.close_event(QCloseEvent())
        assert time.monotonic() - started < 0.5
        assert not release.is_set()
        host.save_current_project_async.assert_not_called()
        # Qt is still able to deliver input/paint events while close waits.
        qapp.processEvents()
    finally:
        release.set()
        assert backend.join(ref.run_id, 2)
    _probe_finished(qapp, lifecycle)
    scheduled.pop()[1]()
    host.save_current_project_async.assert_called_once()
