from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.test_ai_task_history_ui import _record as history_record
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog
from transbridge.ui.tools.ai_translator.task_recovery_prompt import recovery_summary
from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry


class Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.active_version_identity = ("project", "version")


def record(task_id="resume", *, state="running", variant="version"):
    return {
        "task_id": task_id,
        "created_at": "2026-10-04T00:00:00+08:00",
        "project_id": "project",
        "variant_id": variant,
        "mode": "polish",
        "config_digest": "digest",
        "state": state,
        "supported": True,
        "sources": [{"key": "plugin", "label": "Plugin", "translate_keys": [], "polish_keys": []}],
    }


def wait_until(app, condition):
    for _ in range(400):
        app.processEvents()
        if condition():
            return
        QTest.qWait(5)
    raise AssertionError("recovery discovery did not settle")


@pytest.fixture
def setup_registry(tmp_path, qapp):
    app = qapp
    ctx = Context()
    registry = AiTaskRegistry(ctx, directory_provider=lambda: tmp_path)
    yield app, ctx, registry, TaskRecoveryStore(tmp_path / "ai-task-recovery")
    registry.shutdown()
    wait_until(app, lambda: registry.recovery.worker is None)
    for history in registry.history_windows:
        wait_until(app, lambda: not history.busy)
        history.close()


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_discovery_excludes_live_completed_cancelled_and_other_versions(setup_registry):
    app, _ctx, registry, store = setup_registry
    for task_id, state, variant in (
        ("resume", "running", "version"),
        ("live", "running", "version"),
        ("completed", "completed", "version"),
        ("cancelled", "cancelled", "version"),
        ("partial", "partial", "version"),
        ("failed", "failed", "version"),
        ("other", "running", "other-version"),
    ):
        store.create(record(task_id, state=state, variant=variant))
    registry.windows["live"] = SimpleNamespace(request=SimpleNamespace(), run=SimpleNamespace(shutdown=lambda: None))
    wait_until(app, lambda: len(registry.recovery.dialogs) == 1 and registry.recovery.worker is None)
    dialog = registry.recovery.dialogs[0]
    assert dialog.continue_button.text() == "继续任务"
    assert dialog.restart_button.text() == "重新开始"
    assert dialog.later_button.text() == "稍后处理"
    requests = []
    registry.recovery_requested.connect(lambda item, restart: requests.append((item["task_id"], restart)))
    dialog.continue_button.click()
    assert requests == [("resume", False)]


def test_later_keeps_manifest_and_switching_version_discovers_its_task(setup_registry):
    app, ctx, registry, store = setup_registry
    store.create(record())
    store.create(record("second", variant="second"))
    wait_until(app, lambda: len(registry.recovery.dialogs) == 1 and registry.recovery.worker is None)
    registry.recovery.dialogs[0].later_button.click()
    ctx.project_changed.emit()
    QTest.qWait(20)
    assert len(registry.recovery.dialogs) == 1
    assert store.load("resume")["state"] == "running"
    ctx.active_version_identity = ("project", "second")
    ctx.variant_changed.emit("second")
    wait_until(app, lambda: len(registry.recovery.dialogs) == 2 and registry.recovery.worker is None)
    requests = []
    registry.recovery_requested.connect(lambda item, restart: requests.append((item["task_id"], restart)))
    registry.recovery.dialogs[0].continue_button.click()
    assert requests == []
    registry.recovery.dialogs[1].restart_button.click()
    assert requests == [("second", True)]


def test_history_recovers_manifest_only_and_preserves_legacy_read_only(setup_registry, tmp_path):
    app, _ctx, registry, store = setup_registry
    store.create(record("partial", state="partial"))
    old = history_record("old-history")
    TaskHistoryStore(tmp_path / "ai-task-history").save(old)
    dialog = TaskHistoryDialog(registry)
    registry.history_windows.append(dialog)
    wait_until(app, lambda: not dialog.busy and dialog.tasks.topLevelItemCount() == 2)
    requests = []
    registry.recovery_requested.connect(lambda item, restart: requests.append((item["task_id"], restart)))
    for index in range(dialog.tasks.topLevelItemCount()):
        row = dialog.tasks.topLevelItem(index)
        dialog.tasks.setCurrentItem(row)
        if "Plugin" in row.text(1):
            assert dialog.continue_button.isEnabled()
            dialog.continue_button.click()
        else:
            assert not dialog.continue_button.isEnabled()
            assert not dialog.restart_button.isEnabled()
    assert requests == [("partial", False)]
    assert registry.recovery.dialogs == []


def test_unknown_saved_count_is_not_displayed_as_zero():
    value = record()
    assert "0 条（继续" not in recovery_summary(value)
    assert "继续时核验" in recovery_summary(value)
    value["saved_count"] = 42
    assert "已保存校对候选 42 条" in recovery_summary(value)


def test_later_reappears_on_ai_button_without_opening_new_task(setup_registry):
    from transbridge.ui.workbench.widget import WorkbenchWidget

    app, _ctx, registry, store = setup_registry
    store.create(record())
    wait_until(app, lambda: len(registry.recovery.dialogs) == 1 and registry.recovery.worker is None)
    requests = []
    registry.recovery_requested.connect(lambda *args: requests.append(args))
    host = SimpleNamespace(ai_tasks=registry)
    for count in (2, 3):
        registry.recovery.dialogs[-1].later_button.click()
        WorkbenchWidget.open_tool(host, "ai_translator")
        wait_until(app, lambda: len(registry.recovery.dialogs) == count and registry.recovery.worker is None)
        assert registry.recovery.dialogs[-1].isVisible()
        assert not requests
    WorkbenchWidget.open_tool(host, "ai_translator")
    app.processEvents()
    assert len(registry.recovery.dialogs) == 3
    registry.recovery.dialogs[-1].continue_button.click()
    assert len(requests) == 1
    assert not registry.recovery.reoffer_deferred(Mock())


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_deferred_task_is_rechecked_before_reminding(setup_registry, state):
    app, _ctx, registry, store = setup_registry
    store.create(record())
    wait_until(app, lambda: len(registry.recovery.dialogs) == 1 and registry.recovery.worker is None)
    registry.recovery.dialogs[0].later_button.click()
    store.update("resume", state=state)
    open_new = Mock()
    assert registry.recovery.reoffer_deferred(open_new)
    wait_until(app, lambda: open_new.called and registry.recovery.worker is None)
    assert len(registry.recovery.dialogs) == 1
    assert not registry.recovery.reoffer_deferred(open_new)
    open_new.assert_called_once_with()


def test_deferred_reminder_does_not_follow_user_to_other_version(setup_registry):
    app, ctx, registry, store = setup_registry
    store.create(record())
    wait_until(app, lambda: len(registry.recovery.dialogs) == 1 and registry.recovery.worker is None)
    registry.recovery.dialogs[0].later_button.click()
    ctx.active_version_identity = ("project", "other")
    assert not registry.recovery.reoffer_deferred(Mock())


def test_live_resumed_run_blocks_second_recovery(setup_registry):
    _app, _ctx, registry, _store = setup_registry
    registry.windows["new-run"] = SimpleNamespace(
        request=SimpleNamespace(recovery_task_id="original-task"), run=SimpleNamespace(shutdown=lambda: None)
    )
    assert registry.is_recovery_active("original-task")
    assert not registry.request_recovery(record("original-task"))


@pytest.mark.parametrize("restart", [False, True])
def test_workbench_recovery_uses_normal_runtime_and_bypasses_foreground(monkeypatch, qapp, restart):
    from transbridge.ui.tools.ai_translator import ai_translator_window, task_recovery_binding
    from transbridge.ui.workbench.widget import WorkbenchWidget

    opened = []

    class Window:
        def __init__(self):
            self.progress_window_created = Mock()
            self.on_start = Mock()
            self.close = Mock()

        @classmethod
        def open_for_translation(cls, *args, **kwargs):
            window = cls()
            opened.append((window, kwargs))
            return window

    monkeypatch.setattr(ai_translator_window, "AITranslatorWindow", Window)
    configure = Mock()
    monkeypatch.setattr(task_recovery_binding, "configure_recovery", configure)
    show = Mock()
    monkeypatch.setattr("transbridge.ui.windowing.show_and_activate", show)
    manifest = record()
    registry = SimpleNamespace(foreground=Mock(return_value=True), directory_provider=Mock())
    host = SimpleNamespace(
        ai_tasks=registry,
        _pending_ai_recovery=(manifest, restart),
        _ctx=object(),
        _step2=object(),
        _theme_view=None,
        _terminology_profile_controller=None,
        _on_progress_window_created=Mock(),
        _tool_windows={},
    )
    runtime = object()
    WorkbenchWidget.open_tool(host, "ai_translator", task_runtime=runtime)
    window, options = opened[0]
    assert options["task_runtime"] is runtime
    assert options["show_window"] is False
    assert window._task_recovery_registry is registry
    configure.assert_called_once_with(window, manifest, restart=restart)
    if restart:
        show.assert_called_once_with(window)
        window.on_start.assert_not_called()
        window.close.assert_not_called()
    else:
        show.assert_not_called()
        window.on_start.assert_called_once_with()
        window.close.assert_called_once_with()
    registry.foreground.assert_not_called()
    assert host._pending_ai_recovery is None


def test_direct_resume_rejects_bad_config_without_starting(monkeypatch, qapp):
    from PyQt6.QtWidgets import QMessageBox

    from transbridge.ui.tools.ai_translator import task_recovery_binding

    monkeypatch.setattr(task_recovery_binding, "configure_recovery", Mock(side_effect=ValueError("配置不匹配")))
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    window = SimpleNamespace(on_start=Mock(), close=Mock())
    task_recovery_binding.open_recovery(window, record())
    window.on_start.assert_not_called()
    window.close.assert_called_once_with()
    warning.assert_called_once_with(window, "AI 任务未启动", "配置不匹配")


def test_corrupt_recovery_shows_error_without_hiding_old_reports(setup_registry, tmp_path):
    app, _ctx, registry, _store = setup_registry
    folder = tmp_path / "ai-task-recovery"
    folder.mkdir()
    (folder / "broken.json").write_text("{", encoding="utf-8")
    TaskHistoryStore(tmp_path / "ai-task-history").save(history_record("old"))
    dialog = TaskHistoryDialog(registry)
    registry.history_windows.append(dialog)
    wait_until(app, lambda: not dialog.busy and dialog.tasks.topLevelItemCount() == 1)
    wait_until(app, lambda: registry.recovery.worker is None and bool(registry.recovery_error))
    assert "读取恢复信息失败" in dialog.status.text()
    assert not dialog.continue_button.isEnabled()
    assert "读取未完成任务失败" in registry.recovery_error


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_finished_manifest_can_restart_but_cannot_continue(setup_registry, state):
    app, _ctx, registry, store = setup_registry
    store.create(record(state=state))
    dialog = TaskHistoryDialog(registry)
    registry.history_windows.append(dialog)
    wait_until(app, lambda: not dialog.busy and dialog.tasks.topLevelItemCount() == 1)
    assert not dialog.continue_button.isEnabled()
    assert dialog.restart_button.isEnabled()
    requests = []
    registry.recovery_requested.connect(lambda item, restart: requests.append((item["task_id"], restart)))
    assert not registry.request_recovery(record(state=state))
    dialog.restart_button.click()
    assert requests == [("resume", True)]
    registry.windows["old-window"] = SimpleNamespace(
        request=SimpleNamespace(recovery_task_id="resume"), run=SimpleNamespace(state=state, shutdown=lambda: None)
    )
    assert not registry.is_recovery_active("resume")
    assert registry.request_recovery(record(state=state), restart=True)
    registry.windows.clear()


@pytest.mark.parametrize("state,label", [("interrupted", "已中断"), ("completed", "已完成")])
def test_latest_manifest_overrides_older_cancelled_report(setup_registry, tmp_path, state, label):
    app, _ctx, registry, store = setup_registry
    value = record("same-task", state=state)
    value.update(project_saved=state == "completed", applied=state == "completed")
    store.create(value)
    TaskHistoryStore(tmp_path / "ai-task-history").save(history_record("same-task", "cancelled"))
    dialog = TaskHistoryDialog(registry)
    registry.history_windows.append(dialog)
    wait_until(app, lambda: not dialog.busy and dialog.tasks.topLevelItemCount() == 1)
    row = dialog.tasks.topLevelItem(0)
    assert row.text(2) == label
    assert row.text(4) == ("已保存" if state == "completed" else "未应用")


def test_manifest_without_persistence_evidence_does_not_reuse_old_report_state(setup_registry, tmp_path):
    app, _ctx, registry, store = setup_registry
    store.create(record("same-task", state="completed"))
    TaskHistoryStore(tmp_path / "ai-task-history").save(history_record("same-task", "cancelled"))
    dialog = TaskHistoryDialog(registry)
    registry.history_windows.append(dialog)
    wait_until(app, lambda: not dialog.busy and dialog.tasks.topLevelItemCount() == 1)
    assert dialog.tasks.topLevelItem(0).text(4) == "保存状态待核验"
