from __future__ import annotations

import os
from threading import Event
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.application.projects import DirtyDecision
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.coordinators import project_open_operation as operation_module
from transbridge.ui.projection_types import CollectionSlot

_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    _APP.processEvents()
    assert predicate()


def _snapshot(project, revision=1):
    return ProjectionSnapshot(
        f"project:{project}",
        revision,
        revision,
        {
            "project_id": project,
            "project_name": project,
            "variant_id": f"{project}-variant",
            "project_revision": revision,
            "variant_revision": revision,
            "entries": [],
        },
    )


def _failure():
    return OperationResult.failed(DomainError(ErrorCategory.PREREQUISITE, "UNAVAILABLE", "source unavailable"))


@pytest.fixture
def opening(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    store = ProjectionStore(_snapshot("old"))
    context = context_module.AppContext(project_projection=store)
    old_slot = CollectionSlot("old", TranslationEntryCollection([]))
    context.add_slot("old.esp", old_slot)
    started, release = Event(), Event()
    observations = []

    class Workbench:
        enabled = True
        progress = False

        def setEnabled(self, enabled):
            self.enabled = enabled

        def show_step2_progress(self, *_):
            self.progress = True

        def hide_step2_progress(self):
            self.progress = False

    class Opener:
        failure = None
        committed = False

        def activate(self, _prepared, _runtime, *, dirty_decision, prepare_view, expected_active_signature):
            observations.append(("activate", QThread.currentThread()))
            assert dirty_decision is None
            assert expected_active_signature == ("old", 1, "old-variant", 1)
            started.set()
            assert release.wait(3)
            if self.failure is not None:
                return self.failure
            candidate = _snapshot("new")
            payload = prepare_view(candidate)
            observations.append(("prepared", QThread.currentThread()))
            store.rebuild(_snapshot("new"))
            self.committed = True
            return OperationResult.completed({"name": "new", "sources": [], "view_payload": payload})

    host = SimpleNamespace(
        context=context,
        app_runtime=SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _name: store)),
        runtime_context=object(),
        current_project_opener=Opener(),
        project_open_worker=None,
        save_worker=None,
        foreground_worker=None,
        workers=[],
        close_pending=False,
        workbench=Workbench(),
        messages=[],
        successes=[],
        failures=[],
    )
    host.show_message = host.messages.append
    operation = operation_module.ProjectOpenOperation(
        host, lambda *_: pytest.fail("Legacy restore called"), lambda *_: None
    )

    def start(prepare=None):
        return operation.start(
            prepare or (lambda: OperationResult.completed(SimpleNamespace(hydrations=()))),
            dirty_decision=None,
            success_verb="opened",
            show_error_dialog=False,
            on_success=host.successes.append,
            on_failure=lambda *value: host.failures.append(value),
        )

    yield SimpleNamespace(
        host=host,
        context=context,
        store=store,
        old_slot=old_slot,
        operation=operation,
        started=started,
        release=release,
        observations=observations,
        start=start,
    )
    release.set()
    for worker in host.workers:
        worker.wait(4000)
    _APP.processEvents()
    context.close_projection()


def test_blocking_activation_keeps_qt_heartbeat_and_installs_only_on_gui_thread(opening, monkeypatch):
    case = opening
    captures, installed, ticks = [], [], []
    capture = operation_module.prepare_project_view
    monkeypatch.setattr(
        operation_module,
        "prepare_project_view",
        lambda snapshot: (captures.append(QThread.currentThread()), capture(snapshot))[1],
    )
    case.context.project_installed.connect(lambda: installed.append(QThread.currentThread()))
    timer = QTimer()
    timer.setInterval(2)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    try:
        assert case.start()
        assert case.started.wait(1)
        _wait_until(lambda: len(ticks) >= 3)
        assert case.context.project_name == "old"
        assert case.context.active_slot is case.old_slot
        assert not case.host.workbench.enabled
        case.release.set()
        _wait_until(lambda: case.host.project_open_worker is None)
    finally:
        timer.stop()
    assert all(thread is not _APP.thread() for _, thread in case.observations)
    assert captures and all(thread is not _APP.thread() for thread in captures)
    assert installed == [_APP.thread()]
    assert case.context.project_name == "new" and not case.context.slots
    assert not case.context.project_opening and case.host.workbench.enabled
    assert len(case.host.successes) == 1 and not case.host.failures


def test_duplicate_open_stays_rejected_after_worker_exits_until_gui_installation(opening):
    case = opening
    case.release.set()
    assert case.start()
    assert case.operation.worker.wait(2000)
    assert not case.operation.worker.isRunning()
    assert case.context.project_opening
    assert not case.start()
    _wait_until(lambda: case.host.project_open_worker is None)
    assert len(case.host.successes) == 1


@pytest.mark.parametrize("phase", ["prepare", "activate", "view"])
def test_precommit_failure_keeps_old_project_and_slots_and_finishes_operation(opening, monkeypatch, phase):
    case = opening
    if phase == "activate":
        case.host.current_project_opener.failure = _failure()
    if phase == "view":

        def fail_capture(_snapshot):
            raise ValueError("cannot capture labels")

        monkeypatch.setattr(operation_module, "prepare_project_view", fail_capture)
    case.release.set()
    assert case.start(_failure if phase == "prepare" else None)
    _wait_until(lambda: case.host.project_open_worker is None)
    assert case.context.active_slot is case.old_slot and case.context.project_name == "old"
    assert case.store.snapshot().values["project_id"] == "old"
    assert not case.host.current_project_opener.committed
    assert not case.context.project_opening and not case.context.project_view_failed
    assert case.host.workbench.enabled and not case.host.workbench.progress
    assert len(case.host.failures) == 1 and not case.host.successes


@pytest.mark.parametrize("failure", [False, True])
def test_close_pending_does_not_reenable_workbench_or_invoke_navigation_callbacks(opening, failure):
    case = opening
    assert case.start()
    assert case.started.wait(1)
    case.host.close_pending = True
    if failure:
        case.host.current_project_opener.failure = _failure()
    case.release.set()
    _wait_until(lambda: case.host.project_open_worker is None)
    assert not case.host.workbench.enabled
    assert not case.context.project_opening
    assert not case.host.failures and not case.host.successes


def test_failed_ui_install_quarantines_committed_project_and_clears_operation(opening, monkeypatch):
    case = opening

    def fail_install(*_args, **_kwargs):
        raise ValueError("widget installation failed")

    monkeypatch.setattr(case.context, "install_project_open", fail_install)
    case.release.set()
    assert case.start()
    _wait_until(lambda: case.host.project_open_worker is None)
    assert case.host.current_project_opener.committed
    assert case.context.project_view_failed and not case.context.project_opening
    assert not case.host.workbench.enabled
    assert case.host.failures[0][0] == "PROJECT_VIEW_INSTALL_FAILED"
    assert not case.host.successes


def test_revision_published_during_install_is_failure_without_success_or_reenable(opening):
    case = opening
    case.context.collection_changed.connect(lambda _: case.store.rebuild(_snapshot("new", revision=2)))
    case.release.set()
    assert case.start()
    _wait_until(lambda: case.host.project_open_worker is None)
    assert case.context.project_revision == 2 and case.context.project_view_failed
    assert not case.host.workbench.enabled and not case.host.successes
    assert case.host.failures[0][0] == "PROJECT_VIEW_STALE"


def test_worker_start_failure_restores_previous_view_and_releases_token(opening, monkeypatch):
    def cannot_start(_worker):
        raise RuntimeError("cannot start worker")

    monkeypatch.setattr(operation_module.ApiWorker, "start", cannot_start)
    assert not opening.start()
    assert opening.host.project_open_worker is None
    assert not opening.context.project_opening and opening.host.workbench.enabled
    assert opening.context.active_slot is opening.old_slot
    assert opening.host.failures[0][0] == "PROJECT_OPEN_FAILED"


def test_save_failure_stops_open_before_reading_the_target_sources(opening):
    case = opening
    saves = []

    def save(context, *, expected_active_signature):
        saves.append((context, expected_active_signature))
        return _failure()

    case.host.project_commands = SimpleNamespace(save=save)
    assert case.operation.start(
        lambda: pytest.fail("Sources must not be read after a failed save"),
        dirty_decision=DirtyDecision.SAVE,
        success_verb="opened",
        show_error_dialog=False,
        on_failure=lambda *value: case.host.failures.append(value),
    )
    _wait_until(lambda: case.host.project_open_worker is None)

    assert saves == [(case.host.runtime_context, ("old", 1, "old-variant", 1))]
    assert case.context.active_slot is case.old_slot
    assert not case.context.project_opening
    assert not case.host.current_project_opener.committed
    assert case.host.failures[0] == ("UNAVAILABLE", "source unavailable")
