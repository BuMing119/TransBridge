from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget
import pytest

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    OwnerRef,
    TaskActionAvailability,
    TaskCenterAction,
    TaskCenterActionResult,
    TaskCenterItem,
    TaskNavigationIntent,
    TaskRuntime,
)
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
    assert not any(button.isEnabled() for button in (panel._recover, panel._retry, panel._open_result, panel._open_log))

    for index in (1, 2):
        panel._tabs.setCurrentIndex(index)
        assert not any(button.isEnabled() for button in (panel._pause, panel._resume, panel._cancel))
        # Neither a click nor a delayed action may target the hidden current selection.
        panel._cancel.click()
        panel._emit_selected(panel.cancel_requested)
        panel._emit_selected(panel.pause_requested)
        qapp.processEvents()
    panel._tabs.setCurrentIndex(0)
    assert panel._pause.isEnabled()
    assert panel._cancel.isEnabled()
    assert not panel._resume.isEnabled()

    panel._pause.click()
    qapp.processEvents()
    assert not panel._pause.isEnabled()
    assert panel._resume.isEnabled()
    controller.close()


class _TaskCenterActions:
    def __init__(self) -> None:
        all_history_actions = TaskActionAvailability(retry=True, open_result=True, open_log=True)
        self.history = (TaskCenterItem("history", "run-old", "run-old", "旧任务", "failed", 7, all_history_actions),)
        self.recovery = (
            TaskCenterItem(
                "recovery",
                "checkpoint-one",
                "run-checkpoint",
                "可恢复任务",
                "可恢复",
                3,
                TaskActionAvailability(recover=True),
            ),
        )
        self.executed = []

    def list_history(self, _actor, *, retry_context, limit):
        assert retry_context is not None
        assert limit == 100
        return self.history

    def list_recovery(self, _actor):
        return self.recovery

    def execute(self, item, action, _actor, *, retry_context):
        assert retry_context is not None
        self.executed.append((item.key, action))
        if action in {TaskCenterAction.OPEN_RESULT, TaskCenterAction.OPEN_LOG}:
            return TaskCenterActionResult(navigation=TaskNavigationIntent(f"task.{action.value}"))
        return TaskCenterActionResult(job_ref=JobRef(f"new-{action.value}", "gui-owner", f"new-{action.value}"))


def test_task_center_renders_and_routes_only_catalog_available_actions(qapp) -> None:
    tasks = TaskRuntime(id_generator=_Ids(), clock=_Clock())
    actions = _TaskCenterActions()
    use_cases = UseCaseRegistry({
        "task_history": _Catalog(),
        "task_recovery": _Catalog(),
        "task_center_actions": actions,
    })
    runtime = SimpleNamespace(tasks=tasks, use_cases=use_cases)
    context = SimpleNamespace(
        owner_id="gui-owner",
        project_id="project-one",
        variant_id=None,
        session_id="session-one",
        metadata=(
            ("entrypoint", "gui"),
            ("context_ref", "project:project-one"),
            ("context_fingerprint", "fingerprint-one"),
        ),
        permissions=frozenset({"gui"}),
    )
    panel = TaskCenterPanel()
    controller = TaskCenterController(runtime, context, panel)
    navigations = []
    controller.navigation_requested.connect(navigations.append)
    controller.start()

    panel._tabs.setCurrentWidget(panel._history)
    panel._history.setCurrentRow(0)
    assert not any(button.isEnabled() for button in (panel._pause, panel._resume, panel._cancel, panel._recover))
    assert all(button.isEnabled() for button in (panel._retry, panel._open_result, panel._open_log))

    panel._open_result.click()
    qapp.processEvents()
    assert actions.executed[-1] == ("run-old", TaskCenterAction.OPEN_RESULT)
    assert navigations[-1] == TaskNavigationIntent("task.open_result")

    panel._tabs.setCurrentWidget(panel._recovery)
    panel._recovery.setCurrentRow(0)
    assert panel._recover.isEnabled()
    assert not any(button.isEnabled() for button in (panel._retry, panel._open_result, panel._open_log))
    panel._recover.click()
    qapp.processEvents()
    assert actions.executed[-1] == ("checkpoint-one", TaskCenterAction.RECOVER)
    controller.close()


class _TaskPanelStub(QWidget):
    pass


class _SignalStub:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _TaskControllerStub:
    instances = []

    def __init__(self, runtime, runtime_context, panel, *, parent=None):
        self.runtime = runtime
        self.runtime_context = runtime_context
        self.panel = panel
        self.parent = parent
        self.starts = 0
        self.refreshes = 0
        self.navigation_requested = _SignalStub()
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
