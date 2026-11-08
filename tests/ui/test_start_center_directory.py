"""Qt delivery of start-center directory reads under load and shutdown."""

from __future__ import annotations

import os
from threading import Event, get_ident
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectCatalogEntry, ProjectCatalogSnapshot
from transbridge.bootstrap.runtime import UseCaseRegistry
from transbridge.ui.shell.start_center import StartCenterWidget, StartDestinationState
from transbridge.ui.shell.start_center_controller import StartCenterController
from transbridge.ui.worker_registry import get_api_worker_registry

_APP = QApplication.instance() or QApplication([])


def _wait_for(predicate):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start(1)
    deadline.start(5000)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert predicate()


@pytest.fixture
def landing(monkeypatch):
    registry = get_api_worker_registry()
    _wait_for(lambda: not registry.busy)
    entered, release, completed = Event(), Event(), Event()
    scans = []
    recovery_scans = []

    def list_projects():
        scans.append(get_ident())
        if len(scans) == 1:
            entered.set()
            try:
                assert release.wait(10)
            finally:
                completed.set()
        key = f"project-{len(scans)}"
        return ProjectCatalogSnapshot((ProjectCatalogEntry(key, key, f"{key}.json", False, True),))

    def list_recovery(actor):
        recovery_scans.append((get_ident(), actor.owner_id))
        return ()

    stack = QStackedWidget()
    stack.addWidget(QWidget())
    view = StartCenterWidget()
    stack.addWidget(view)
    use_cases = UseCaseRegistry({
        "project_catalog": SimpleNamespace(list_projects=list_projects),
        "task_recovery": SimpleNamespace(list=list_recovery),
    })
    host = SimpleNamespace(
        context=SimpleNamespace(project_name=None, dirty=False, active_project_id=None),
        app_runtime=SimpleNamespace(use_cases=use_cases),
        runtime_context=RequestContext("test-user"),
        close_pending=False,
        mode_tabs=stack,
        central_stack=stack,
    )
    controller = StartCenterController(host, view)
    states = []
    render = view.render

    def record(state):
        assert get_ident() == gui_thread
        states.append(state)
        render(state)

    gui_thread = get_ident()
    monkeypatch.setattr(view, "render", record)
    yield SimpleNamespace(**locals())
    release.set()
    _wait_for(lambda: not registry.busy)
    stack.close()


def test_restoring_renders_before_slow_reads_and_refreshes_only_latest_landing(landing):
    heartbeats = []
    landing.controller.show_restoring()
    QTimer.singleShot(0, lambda: heartbeats.append(True))

    assert landing.states[-1].destination is StartDestinationState.RESTORING_LAST
    assert landing.states[-1].recent_projects == ()
    _wait_for(lambda: landing.entered.is_set() and heartbeats)
    assert not landing.completed.is_set()
    assert landing.registry.busy
    assert landing.scans[0] != get_ident()

    for _ in range(5):
        landing.controller.show_empty()
    assert len(landing.scans) == 1
    landing.release.set()
    _wait_for(lambda: not landing.registry.busy)

    delivered = [row.project_key for state in landing.states for row in state.recent_projects]
    assert delivered == ["project-2"]
    assert len(landing.scans) == len(landing.recovery_scans) == 2
    assert all(thread != get_ident() and owner == "test-user" for thread, owner in landing.recovery_scans)
    assert landing.states[-1].destination is StartDestinationState.START_CENTER_EMPTY


@pytest.mark.parametrize("departure", ["workbench", "closing"])
def test_late_directory_read_does_not_render_after_navigation_or_close(landing, departure):
    landing.controller.show_empty()
    _wait_for(landing.entered.is_set)
    if departure == "workbench":
        # The navigation rail may switch the page directly.
        landing.stack.setCurrentIndex(0)
    else:
        landing.host.close_pending = True
    current = landing.stack.currentWidget()
    before = len(landing.states)
    landing.release.set()
    _wait_for(lambda: not landing.registry.busy)

    assert landing.stack.currentWidget() is current
    assert len(landing.states) == before
