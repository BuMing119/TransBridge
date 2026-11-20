from __future__ import annotations

from dataclasses import fields

# ruff: noqa: E402 - configure headless Qt before importing widgets.
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow, QWidget

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects.source_update_models import (
    BatchSourceUpdatePreview,
    SourceUpdateDifference,
    SourceUpdateTarget,
)
from transbridge.ui.coordinators import source_update_coordinator as module
from transbridge.ui.coordinators.source_update_coordinator import SourceUpdateCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.source_update_dialog import SourceUpdateDialog

_APP = QApplication.instance() or QApplication([])


def _host(monkeypatch):
    events = []
    host = QWidget()
    host.context = SimpleNamespace(active_project_id="active")
    host.runtime_context = RequestContext("owner", project_id="active", variant_id="variant")
    source = SourceUpdateTarget("source", "old.esp", FormatId.PLUGIN_SSE, "插件")

    def list_sources(path, context):
        events.append(("list", path, context.project_id, context.variant_id))
        return OperationResult.completed((source,))

    def prepare(path, pairs, context):
        events.append(("prepare", path, pairs, context.project_id))
        return OperationResult.completed(_preview())

    def commit(token, context):
        events.append(("commit", token, context.project_id))
        return OperationResult.completed(SimpleNamespace(project_path="target.json", backup_path="backup.json"))

    service = SimpleNamespace(
        active_project_path=lambda _context: "active.json",
        list_sources=list_sources,
        prepare_batch=prepare,
        commit=commit,
        discard=lambda token, context: events.append(("discard", token, context.project_id)),
    )
    host.app_runtime = SimpleNamespace(
        use_cases=SimpleNamespace(names=lambda: ("project_source_updates",), resolve=lambda _name: service)
    )

    def dispatch(operation, **kwargs):
        events.append(("worker", kwargs["message"]))
        kwargs["on_result"](operation())
        return True

    def save(*, on_finished):
        events.append(("save",))
        on_finished(True)
        return True

    host.start_foreground_task = dispatch
    host.save_current_project_async = save
    host.show_message = lambda text: events.append(("message", text))
    host.project_coordinator = SimpleNamespace(open_project_path=lambda path: events.append(("open", path)))
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: events.append(("warning", args[-1])))
    return host, events, service


def _preview():
    return BatchSourceUpdatePreview(
        "prepared", 3, (SourceUpdateDifference("test.esp", "old.esp", "new.esp", 2, 1, 4, 5, 6, 7),)
    )


def _select_replacement(coordinator):
    dialog = coordinator._dialog
    dialog._assign("source", "new.esp")
    dialog.primary_button.click()
    return dialog


def test_binary_eet_source_update_chooses_native_eet_file_filter(monkeypatch):
    host, _events, service = _host(monkeypatch)
    source = SourceUpdateTarget("source", "old.eet", FormatId.BINARY_EET, "EET")
    service.list_sources = lambda *_args: OperationResult.completed((source,))
    filters = []

    def choose(_parent, _title, _directory, file_filter):
        filters.append(file_filter)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    dialog = coordinator._dialog
    dialog._browse(dialog._rows[0])
    assert filters == ["ESP-ESM Translator EET (*.eet);;所有文件 (*)"]
    dialog.reject()
    host.close()


def test_preview_explains_all_variant_and_unverified_impact():
    dialog = SourceUpdateDialog((), "工程")
    dialog.show_preview(_preview())
    preview = dialog.preview_page
    assert "全部 3 个翻译版本" in preview.summary.text()
    assert "无法核验 6" in preview.summary.text()
    row = preview.differences.topLevelItem(0)
    assert row.isExpanded()
    assert row.child(1).text(1) == "new.esp"
    assert "顺序变化 7" in row.child(2).text(1)
    assert dialog.primary_button.text() == "更新 1 个来源"
    dialog.close()


def test_project_menu_exposes_source_update_with_its_own_intent():
    calls = []
    callbacks = {field.name: lambda: None for field in fields(MenuCallbacks)}
    callbacks["update_source"] = lambda: calls.append("update")
    window = QMainWindow()
    MenuBuilder(window, MenuCallbacks(**callbacks)).build()
    project_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "工程")
    update = next(action for action in project_menu.actions() if action.data() == IntentId.PROJECT_SOURCE_UPDATE.value)
    assert update.text() == "更新源文件…"
    update.trigger()
    assert calls == ["update"]
    window.close()


def test_current_project_saves_before_prepare_and_reopens_after_commit(monkeypatch):
    host, events, _service = _host(monkeypatch)
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    _select_replacement(coordinator).primary_button.click()
    operations = [event[0] for event in events]
    assert operations.index("save") < operations.index("prepare") < operations.index("commit")
    assert ("open", "target.json") in events
    assert not coordinator._busy
    host.close()


def test_cancel_discards_preview_without_committing(monkeypatch):
    host, events, _service = _host(monkeypatch)
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    _select_replacement(coordinator).reject()
    assert ("discard", "prepared", "active") in events
    assert not any(event[0] in {"commit", "open"} for event in events)
    assert not coordinator._busy
    host.close()


def test_recovery_update_targets_detached_project_and_preserves_active_workbench(monkeypatch):
    host, events, _service = _host(monkeypatch)
    recovery = SimpleNamespace(
        project_path="recovery.json",
        name="待恢复工程",
        variant=SimpleNamespace(ref=SimpleNamespace(project_id=SimpleNamespace(value="recovery"))),
    )
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_recovery(recovery)
    _select_replacement(coordinator).primary_button.click()
    assert ("list", "recovery.json", "recovery", None) in events
    assert ("commit", "prepared", "recovery") in events
    assert not any(event[0] in {"save", "open"} for event in events)
    assert host.context.active_project_id == "active"
    host.close()


def test_failed_prepare_surfaces_diagnostic_and_does_not_commit(monkeypatch):
    host, events, service = _host(monkeypatch)
    service.prepare_batch = lambda *args: OperationResult.failed(
        DomainError(ErrorCategory.CONFLICT, "SOURCE_CHANGED", "源文件在预览期间发生变化。")
    )
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    dialog = _select_replacement(coordinator)
    assert "SOURCE_CHANGED" in dialog.feedback.text()
    assert dialog.primary_button.isEnabled()
    assert not any(event[0] in {"commit", "open"} for event in events)
    dialog.reject()
    assert not coordinator._busy
    host.close()


def test_back_discards_preview_and_keeps_pairing(monkeypatch):
    host, events, _service = _host(monkeypatch)
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    dialog = _select_replacement(coordinator)
    dialog.back_button.click()
    assert ("discard", "prepared", "active") in events
    assert dialog.table.item(0, 1).toolTip() == "new.esp"
    assert dialog.primary_button.text() == "预览差异"
    assert dialog.primary_button.isEnabled()
    dialog.reject()
    host.close()


def test_folder_discovery_can_skip_ambiguous_rows_and_preview_the_rest(monkeypatch, tmp_path):
    host, events, service = _host(monkeypatch)
    sources = (
        SourceUpdateTarget("one", "old/old.esp", FormatId.PLUGIN_SSE, "旧版"),
        SourceUpdateTarget("two", "old/effects.esp", FormatId.PLUGIN_SSE, "特效"),
        SourceUpdateTarget("three", "old/unselected.esp", FormatId.PLUGIN_SSE, "未选"),
    )
    service.list_sources = lambda *_args: OperationResult.completed(sources)
    for filename in ("old.esp", "a/effects.esp", "b/effects.esp"):
        path = tmp_path / filename
        path.parent.mkdir(exist_ok=True)
        path.touch()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(tmp_path))
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    dialog = coordinator._dialog
    dialog._choose_directory()
    assert dialog.table.item(0, 2).text() == "已配对"
    assert dialog.table.item(1, 1).text() == "发现 2 个同名文件"
    assert dialog.table.item(2, 2).text() == "未配对"
    assert not dialog.primary_button.isEnabled()
    dialog._row_buttons[1][1].click()
    assert dialog.table.item(1, 2).text() == "已跳过"
    dialog.primary_button.click()
    assert ("prepare", "active.json", (("one", str(tmp_path / "old.esp")),), "active") in events
    assert dialog.pages.currentWidget() is dialog.preview_page
    dialog.reject()
    host.close()


def test_same_basename_in_two_sources_requires_explicit_pairing(monkeypatch):
    host, _events, service = _host(monkeypatch)
    sources = (
        SourceUpdateTarget("one", "a/shared.esp", FormatId.PLUGIN_SSE, "一"),
        SourceUpdateTarget("two", "b/shared.esp", FormatId.PLUGIN_SSE, "二"),
    )
    service.list_sources = lambda *_args: OperationResult.completed(sources)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: (["new/shared.esp"], ""))
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    dialog = coordinator._dialog
    dialog.files_button.click()
    assert [dialog.table.item(row, 2).text() for row in (0, 1)] == ["待选择", "待选择"]
    dialog._assign("two", "new/shared.esp")
    dialog._assign("one", "new/shared.esp")
    assert "已配对到另一项" in dialog.feedback.text()
    assert not dialog.primary_button.isEnabled()
    dialog.reject()
    host.close()
