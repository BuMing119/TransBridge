from __future__ import annotations

from dataclasses import fields

# ruff: noqa: E402 - configure headless Qt before importing widgets.
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.ui.coordinators import source_update_coordinator as module
from transbridge.ui.coordinators.source_update_coordinator import SourceUpdateCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.menu_builder import MenuBuilder, MenuCallbacks
from transbridge.ui.source_update_preview import SourceUpdatePreviewDialog

_APP = QApplication.instance() or QApplication([])


def _preview():
    return SimpleNamespace(
        token="prepared",
        source_name="test.esp",
        replacement_path="new.esp",
        variant_count=3,
        added=2,
        removed=1,
        unchanged=4,
        changed=5,
        unverified=6,
        reordered=7,
        warnings=("旧原文无法核验，需要复核。",),
    )


def _host(monkeypatch, *, confirm=True):
    events = []
    host = QWidget()
    host.context = SimpleNamespace(active_project_id="active")
    host.runtime_context = RequestContext("owner", project_id="active", variant_id="variant")
    source = SimpleNamespace(source_id="source", location="old.esp", format_id="plugin.sse", label="插件")

    def list_sources(path, context):
        events.append(("list", path, context.project_id, context.variant_id))
        return OperationResult.completed((source,))

    def prepare(path, source_id, replacement, context):
        events.append(("prepare", path, source_id, replacement, context.project_id))
        return OperationResult.completed(_preview())

    def commit(token, context):
        events.append(("commit", token, context.project_id))
        return OperationResult.completed(SimpleNamespace(project_path="target.json", backup_path="backup.json"))

    service = SimpleNamespace(
        active_project_path=lambda _context: "active.json",
        list_sources=list_sources,
        prepare=prepare,
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

    host.start_foreground_task = dispatch
    host.save_current_project_async = save
    host.show_message = lambda text: events.append(("message", text))
    host.project_coordinator = SimpleNamespace(open_project_path=lambda path: events.append(("open", path)))
    monkeypatch.setattr(module.QInputDialog, "getItem", lambda *args: (args[3][0], True))
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args: ("new.esp", ""))
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: events.append(("warning", args[-1])))
    monkeypatch.setattr(
        module,
        "SourceUpdatePreviewDialog",
        lambda *args: SimpleNamespace(
            exec=lambda: QDialog.DialogCode.Accepted if confirm else QDialog.DialogCode.Rejected,
            deleteLater=lambda: None,
        ),
    )
    return host, events


def test_preview_explains_all_variant_and_unverified_impact():
    dialog = SourceUpdatePreviewDialog(_preview())
    assert "全部 3 个翻译版本" in dialog.summary.text()
    assert "原文无法核验 6" in dialog.summary.text()
    assert "备份" in dialog.details.toPlainText()
    assert "ParaTranz" in dialog.details.toPlainText()
    assert "旧原文无法核验" in dialog.details.toPlainText()
    assert dialog.confirm_button.text() == "确认更新全部版本"
    dialog.close()


def test_project_menu_exposes_source_update_with_its_own_intent():
    calls = []
    callbacks = {field.name: lambda: None for field in fields(MenuCallbacks)}
    callbacks["update_source"] = lambda: calls.append("update")
    window = QMainWindow()
    MenuBuilder(window, MenuCallbacks(**callbacks)).build()
    project_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "项目")
    update = next(action for action in project_menu.actions() if action.data() == IntentId.PROJECT_SOURCE_UPDATE.value)
    assert update.text() == "更新源文件…"
    update.trigger()
    assert calls == ["update"]
    window.close()


def test_current_project_saves_before_prepare_and_reopens_after_commit(monkeypatch):
    host, events = _host(monkeypatch)
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    operations = [event[0] for event in events]
    assert operations.index("save") < operations.index("prepare") < operations.index("commit")
    assert ("open", "target.json") in events
    assert not coordinator._busy
    host.close()


def test_cancel_discards_preview_without_committing(monkeypatch):
    host, events = _host(monkeypatch, confirm=False)
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    assert ("discard", "prepared", "active") in events
    assert not any(event[0] in {"commit", "open"} for event in events)
    assert not coordinator._busy
    host.close()


def test_recovery_update_targets_detached_project_and_preserves_active_workbench(monkeypatch):
    host, events = _host(monkeypatch)
    recovery = SimpleNamespace(
        project_path="recovery.json",
        variant=SimpleNamespace(ref=SimpleNamespace(project_id=SimpleNamespace(value="recovery"))),
    )
    SourceUpdateCoordinator(host).start_recovery(recovery)
    assert ("list", "recovery.json", "recovery", None) in events
    assert ("commit", "prepared", "recovery") in events
    assert not any(event[0] in {"save", "open"} for event in events)
    assert host.context.active_project_id == "active"
    host.close()


def test_failed_prepare_surfaces_diagnostic_and_does_not_commit(monkeypatch):
    from transbridge.application.contracts import DomainError, ErrorCategory

    host, events = _host(monkeypatch)
    service = host.app_runtime.use_cases.resolve("project_source_updates")
    service.prepare = lambda *args: OperationResult.failed(
        DomainError(ErrorCategory.CONFLICT, "SOURCE_CHANGED", "源文件在预览期间发生变化。")
    )
    coordinator = SourceUpdateCoordinator(host)
    coordinator.start_current()
    assert any(event[0] == "warning" and "SOURCE_CHANGED" in event[1] for event in events)
    assert not any(event[0] in {"commit", "open"} for event in events)
    assert not coordinator._busy
    host.close()
