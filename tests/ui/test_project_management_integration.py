"""The history list operates on real project snapshots through its coordinator."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from tests.ui.test_v2_project_transfer import host_for
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.ui import context as context_module
from transbridge.ui.coordinators.project_transfer_coordinator import ProjectTransferCoordinator
from transbridge.ui.project_management_dialogs import SnapshotHistoryDialog


def test_history_list_creates_restores_and_deletes_real_snapshots(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token="", user_id=None)
    )
    host, services = host_for(tmp_path / "history")

    def start(operation, *, on_result=None, on_error=None, on_finished=None, before_start=None, **_kwargs):
        if before_start is not None:
            before_start()
        result = operation()
        if on_result is not None:
            on_result(result)
        if on_finished is not None:
            on_finished()
        return True

    def confirm_delete(_parent, title, *_args):
        assert title == "确认删除历史还原点"
        return QMessageBox.StandardButton.Yes

    host.start_foreground_task = start
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("批量修改前", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", confirm_delete)
    dialog = None
    try:
        commands = services.gui_project_commands
        request = host.runtime_context
        assert commands.create_project(ProjectProvisioningRequest("历史列表验证"), request).is_success
        assert commands.replace_labels({}, {"original": {"name": "原始标签"}}, request).is_success
        app.processEvents()
        transfer = ProjectTransferCoordinator(host)
        dialog = SnapshotHistoryDialog(host, host.context, transfer=transfer)
        dialog.show()
        assert dialog._snapshots.count() == 0
        assert not dialog._restore.isEnabled()

        dialog._create.click()
        app.processEvents()
        assert dialog._snapshots.count() == 1
        original = dialog._snapshots.item(0).data(Qt.ItemDataRole.UserRole)
        assert original.name == "批量修改前"
        assert commands.replace_labels({}, {"changed": {"name": "修改后的标签"}}, request).is_success
        app.processEvents()

        dialog._snapshots.setCurrentRow(0)
        dialog._restore.click()
        app.processEvents()
        restored = services.project_lifecycle.active.variant.snapshot().to_dto().envelope.data
        assert restored["label_library"] == {"original": {"name": "原始标签"}}
        snapshots = services.project_snapshots.list(request)
        assert len(snapshots) == dialog._snapshots.count() == 2
        backup = next(item for item in snapshots if item.identity != original.identity)
        assert backup.name.startswith("恢复前-")

        for row in range(dialog._snapshots.count()):
            if dialog._snapshots.item(row).data(Qt.ItemDataRole.UserRole).identity == original.identity:
                dialog._snapshots.setCurrentRow(row)
                break
        dialog._delete.click()
        app.processEvents()
        assert [item.identity for item in services.project_snapshots.list(request)] == [backup.identity]
        assert dialog._snapshots.count() == 1
        assert services.project_lifecycle.active.variant.snapshot().to_dto().envelope.data == restored
    finally:
        if dialog is not None:
            dialog.close()
        host.close()
        host.context.close_projection()
        services.close()
