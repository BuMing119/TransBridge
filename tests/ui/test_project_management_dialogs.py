from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QInputDialog, QMainWindow
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.projects.snapshots import ProjectSnapshotInfo
from transbridge.ui.coordinators.project_dialogs import ProjectDialogs
from transbridge.ui.coordinators.variant_coordinator import VariantCoordinator
from transbridge.ui.project_management_dialogs import (
    ProjectPropertiesDialog,
    SnapshotHistoryDialog,
    VariantManagementDialog,
)
from transbridge.ui.shell.action_catalog import IntentId


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)
    workspace_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.active_project_id = "project"
        self.active_variant_id = "a"
        self.project_name = "示例工程"
        self.project_opening = False
        self.project_view_failed = False
        self.project_variants = ({"id": "a", "name": "初版"}, {"id": "b", "name": "校对版"})

    @property
    def active_version_identity(self):
        return self.active_project_id, self.active_variant_id

    def activate_variant(self, identity):
        self.active_variant_id = identity
        self.variant_changed.emit(identity)


class _Transfer:
    def __init__(self):
        self.reads = []
        self.pending = []
        self.accept = True

    def list_snapshots(self, scope, **callbacks):
        self.reads.append((scope, callbacks))
        return self.accept

    def complete(self, snapshots=(), error=None):
        _scope, callbacks = self.reads[-1]
        if error:
            callbacks["on_error"](error)
        else:
            callbacks["on_result"](snapshots)
        callbacks["on_finished"]()

    def save_snapshot(self, *, scope, on_finished):
        self.pending.append(("create", None, scope, on_finished))
        return True

    def restore_snapshot(self, snapshot, scope, *, on_finished):
        self.pending.append(("restore", snapshot.identity, scope, on_finished))
        return True

    def remove_snapshot(self, snapshot, scope, *, on_finished):
        self.pending.append(("remove", snapshot.identity, scope, on_finished))
        return True


def test_version_list_dispatches_existing_commands_and_refreshes_on_projection(app):
    context = _Context()
    dispatched = []
    switched = []
    dialog = VariantManagementDialog(
        None,
        context,
        dispatch=dispatched.append,
        switch=switched.append,
        availability=lambda _intent: SimpleNamespace(
            enabled=not context.project_opening,
            reason="工程还在开启" if context.project_opening else None,
        ),
    )
    try:
        assert dialog._versions.count() == 2
        assert dialog._versions.currentItem().text() == "初版（当前）"
        assert not dialog._activate.isEnabled()
        dialog._versions.setCurrentRow(1)
        dialog._activate.click()
        dialog._create.click()
        dialog._copy.click()
        dialog._clear.click()
        assert switched == ["b"]
        assert dispatched == [
            IntentId.PROJECT_VARIANT_CREATE,
            IntentId.PROJECT_VARIANT_COPY,
            IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR,
        ]
        context.project_variants += ({"id": "c", "name": "复制版"},)
        context.activate_variant("c")
        app.processEvents()
        assert [dialog._versions.item(i).text() for i in range(3)] == ["初版", "校对版", "复制版（当前）"]
        context.project_opening = True
        dialog.refresh()
        assert not dialog._clear.isEnabled()
        context.active_project_id = "other"
        context.project_opening = False
        context.project_changed.emit()
        app.processEvents()
        assert not dialog._create.isEnabled()
        assert dialog._versions.count() == 0
    finally:
        dialog.close()


@pytest.mark.parametrize("error", [None, "读取文件失败"])
def test_closed_history_window_ignores_late_callbacks(app, error):
    transfer = _Transfer()
    dialog = SnapshotHistoryDialog(None, _Context(), transfer=transfer)
    dialog.show()
    dialog.close()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(dialog)
    transfer.complete((ProjectSnapshotInfo("id", "一个还原点", 1),), error=error)


def test_history_discards_old_result_and_only_explicit_refresh_reloads_original_version(app):
    context = _Context()
    transfer = _Transfer()
    dialog = SnapshotHistoryDialog(None, context, transfer=transfer)
    try:
        assert transfer.reads[0][0] == ("project", "a")
        assert "初版" in dialog._target.text()
        assert not dialog._create.isEnabled()
        context.activate_variant("b")
        context.activate_variant("a")
        transfer.complete((ProjectSnapshotInfo("stale", "陈旧的还原点", 1),))
        app.processEvents()
        assert dialog._snapshots.count() == 0
        assert not dialog._restore.isEnabled()
        assert "刷新" in dialog._status.text()
        dialog._reload.click()
        assert len(transfer.reads) == 2
        transfer.complete(())
        assert "尚无历史还原点" in dialog._status.text()
        assert dialog._create.isEnabled()
    finally:
        dialog.close()


def test_history_read_failure_and_busy_rejection_allow_a_retry(app):
    transfer = _Transfer()
    transfer.accept = False
    dialog = SnapshotHistoryDialog(None, _Context(), transfer=transfer)
    try:
        assert dialog._reload.isEnabled()
        assert "另一项后台操作" in dialog._status.text()
        transfer.accept = True
        dialog._reload.click()
        transfer.complete(error="模拟失败")
        assert "模拟失败" in dialog._status.text()
        assert dialog._reload.isEnabled()
        dialog._reload.click()
        transfer.complete((ProjectSnapshotInfo("id", "备份", 1),))
        dialog._snapshots.setCurrentRow(0)
        assert dialog._restore.isEnabled()
    finally:
        dialog.close()


@pytest.mark.parametrize(
    ("button", "verb", "identity"),
    [
        ("_create", "create", None),
        ("_restore", "restore", "id-2"),
        ("_delete", "remove", "id-2"),
    ],
)
def test_history_operations_use_selected_identity_and_reload_after_completion(app, button, verb, identity):
    transfer = _Transfer()
    dialog = SnapshotHistoryDialog(None, _Context(), transfer=transfer)
    try:
        transfer.complete((ProjectSnapshotInfo("id-1", "同名", 1), ProjectSnapshotInfo("id-2", "同名", 1)))
        dialog._snapshots.setCurrentRow(1)
        getattr(dialog, button).click()
        [(operation, selected, scope, finished)] = transfer.pending
        assert (operation, selected, scope) == (verb, identity, ("project", "a"))
        assert not dialog._create.isEnabled()
        assert len(transfer.reads) == 1
        finished()
        assert len(transfer.reads) == 2
        assert not dialog._restore.isEnabled()
    finally:
        dialog.close()


def test_properties_dialog_reuses_intents_and_refreshes_project_name(app):
    host = QMainWindow()
    host.context = _Context()
    opened = []
    dialogs = ProjectDialogs(
        host,
        dispatch=opened.append,
        availability=lambda _intent: SimpleNamespace(enabled=True, reason=None),
    )
    try:
        dialogs.open_properties()
        app.processEvents()
        dialog = host.findChild(ProjectPropertiesDialog)
        assert dialog is not None and dialog.isVisible()
        dialogs.open_properties()
        app.processEvents()
        assert host.findChildren(ProjectPropertiesDialog) == [dialog]
        dialog._rename.click()
        dialog._encoding.click()
        assert opened == [IntentId.PROJECT_RENAME, IntentId.PROJECT_TEXT_ENCODING]
        host.context.project_name = "已重命名"
        host.context.project_changed.emit()
        app.processEvents()
        assert "已重命名" in dialog._target.text()
        host.context.active_project_id = "other"
        host.context.project_changed.emit()
        app.processEvents()
        assert not dialog._rename.isEnabled() and not dialog._encoding.isEnabled()
    finally:
        dialogs.close()
        host.close()


def test_copy_does_not_run_in_a_different_version_after_pending_save(app, monkeypatch):
    context = _Context()
    context.dirty = True
    pending = []
    messages = []
    parent = QMainWindow()
    parent.context = context
    parent.project_commands = object()
    parent.runtime_context = RequestContext("test")
    parent.save_current_project_async = lambda *, on_finished: pending.append(on_finished)
    parent.start_foreground_task = lambda *_args, **_kwargs: pytest.fail("不得向新版执行旧复制操作")
    parent.show_message = messages.append
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("复制版本", True))
    try:
        VariantCoordinator(parent).copy_variant()
        context.activate_variant("b")
        pending[0](True)
        assert "已变化" in messages[0]
    finally:
        parent.close()
