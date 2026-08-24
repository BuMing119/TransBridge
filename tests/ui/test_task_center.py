from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.tasks import JobCapabilities, JobSpec, OwnerRef, TaskRuntime
from transbridge.bootstrap.runtime import UseCaseRegistry
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
