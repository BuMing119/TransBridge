from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget
import pytest

from transbridge.application.tasks import JobCapabilities, JobSpec, OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import UseCaseRegistry
from transbridge.ui.shell import intent_composition as intent_composition_module
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.shell.overlay_geometry import workspace_overlay_rect
from transbridge.ui.shell.task_center import TaskCenterController, TaskCenterPanel


class _Ids:
    def __init__(self):
        self.value = 0

    def new_id(self):
        self.value += 1
        return f"task-{self.value}"


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


class _Catalog:
    def list(self, _actor, **_kwargs):
        return ()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_task_center_tracks_runtime_events_and_only_enables_real_actions(qapp):
    tasks = TaskRuntime(id_generator=_Ids(), clock=_Clock())
    use_cases = UseCaseRegistry({"task_history": _Catalog(), "task_recovery": _Catalog()})
    runtime = SimpleNamespace(tasks=tasks, use_cases=use_cases)
    context = SimpleNamespace(
        owner_id="gui-owner",
        metadata=(("entrypoint", "gui"),),
        permissions=frozenset({"gui"}),
    )
    panel = TaskCenterPanel()
    assert panel.accessibleName() == "任务活动中心"
    assert all(button.accessibleName() for button in (panel._pause, panel._resume, panel._cancel))
    controller = TaskCenterController(runtime, context, panel)
    controller.start()
    owner = OwnerRef("feature-owner", "ui.ai-translator")
    ref = tasks.submit(
        JobSpec(
            "ai-translation",
            "project:one",
            "fingerprint",
            "AI 翻译",
            capabilities=JobCapabilities(supports_pause=True, supports_resume=True),
        ),
        owner,
    ).ref
    tasks.start(ref, owner)
    qapp.processEvents()

    assert panel._current.count() == 1
    panel._current.setCurrentRow(0)
    assert panel._pause.isEnabled()
    assert panel._cancel.isEnabled()
    assert not panel._resume.isEnabled()

    panel._pause.click()
    qapp.processEvents()
    assert not panel._pause.isEnabled()
    assert panel._resume.isEnabled()
    controller.close()


class _TaskPanelStub(QWidget):
    pass


class _TaskControllerStub:
    instances = []

    def __init__(self, runtime, runtime_context, panel, *, parent=None):
        self.runtime = runtime
        self.runtime_context = runtime_context
        self.panel = panel
        self.parent = parent
        self.starts = 0
        self.refreshes = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.starts += 1

    def refresh_catalogs(self) -> None:
        self.refreshes += 1


def test_task_center_is_reused_as_overlay_without_resizing_main_content(qapp, monkeypatch) -> None:
    _TaskControllerStub.instances.clear()
    monkeypatch.setattr(intent_composition_module, "TaskCenterPanel", _TaskPanelStub)
    monkeypatch.setattr(intent_composition_module, "TaskCenterController", _TaskControllerStub)
    host = QMainWindow()
    host.app_runtime = object()
    host.runtime_context = object()
    central = QWidget()
    host.setCentralWidget(central)
    host.resize(1280, 720)
    host.show()
    qapp.processEvents()
    central_geometry = central.geometry()

    composition = ShellIntentComposition.__new__(ShellIntentComposition)
    composition._host = host
    composition._task_dock = None
    composition._task_center = None
    composition._task_overlay_host_geometry = None
    composition._show_task_center()
    qapp.processEvents()

    dock = composition._task_dock
    controller = composition._task_center
    assert dock.parent() is host
    assert dock.isFloating()
    assert dock.windowFlags() & Qt.WindowType.Window
    assert dock.allowedAreas() == Qt.DockWidgetArea.NoDockWidgetArea
    assert dock.features() == QDockWidget.DockWidgetFeature.DockWidgetClosable
    assert host.dockWidgetArea(dock) == Qt.DockWidgetArea.NoDockWidgetArea
    assert central.geometry() == central_geometry
    assert dock.size() == workspace_overlay_rect(host.rect()).size()
    assert controller.starts == 1

    dock.close()
    qapp.processEvents()
    assert not dock.isVisible()
    composition._show_task_center()
    qapp.processEvents()
    assert composition._task_dock is dock
    assert dock.isVisible()
    assert controller.refreshes == 1
    assert central.geometry() == central_geometry

    dock.hide()
    host.resize(1600, 900)
    qapp.processEvents()
    resized_central_geometry = central.geometry()
    composition._show_task_center()
    qapp.processEvents()
    assert dock.size() == workspace_overlay_rect(host.rect()).size()
    assert central.geometry() == resized_central_geometry

    dock.close()
    dock.deleteLater()
    host.close()
