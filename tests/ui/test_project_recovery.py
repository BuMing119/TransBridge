from __future__ import annotations

# ruff: noqa: E402 - configure the headless Qt platform before importing Qt.
import json
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, OperationResult
from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.persistence.project_recovery import ProjectRecoverySnapshot
from transbridge.persistence.v2 import (
    ProjectId,
    SourceFingerprint,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.project_recovery import ProjectRecoveryDialog

_APP = QApplication.instance() or QApplication([])


def _recovery():
    namespace = SourceNamespace("source:recovery")
    return ProjectRecoverySnapshot(
        "D:/project.json",
        "恢复测试",
        VariantSnapshot(
            VariantRef(VariantId("v"), ProjectId("p")),
            (SourceFingerprint(namespace, "a" * 64),),
            (
                VariantEntryState(EntryKey(namespace, "key-a"), "<script>文本</script>", 5, ("reviewed",)),
                VariantEntryState(EntryKey(namespace, "key-b"), "第二条译文", 1),
            ),
        ),
        (Diagnostic("RESOURCE_NOT_FOUND", "来源不存在。", DiagnosticSeverity.WARNING),),
    )


def test_recovery_table_is_read_only_and_copies_exact_saved_state():
    recovery = _recovery()
    dialog = ProjectRecoveryDialog(recovery)
    model = dialog.table.model()
    assert model.rowCount() == 2
    assert not model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable
    assert model.data(model.index(0, 2)) == "<script>文本</script>"
    assert not dialog.copy_button.isEnabled()

    dialog.table.selectRow(0)
    assert dialog.copy_button.isEnabled()
    dialog.copy_button.click()

    assert json.loads(QApplication.clipboard().text()) == [recovery.variant.entries[0].to_dict()]
    dialog.close()


def test_project_coordinator_keeps_existing_workbench_when_showing_recovery():
    recovery = _recovery()
    successes = []
    messages = []
    progress = []
    host = QWidget()
    host.project_open_worker = None
    host.save_worker = None
    host.foreground_worker = None
    host.workers = []
    host.runtime_context = object()
    host.current_project_opener = SimpleNamespace(
        activate=lambda *_args, **_kwargs: OperationResult.completed({"recovery": recovery, "read_only": True})
    )
    host.workbench = SimpleNamespace(
        show_step2_progress=lambda *_args: progress.append("shown"),
        hide_step2_progress=lambda: progress.append("hidden"),
    )
    host.show_message = messages.append
    coordinator = ProjectCoordinator(host)

    def forbidden_restore(*_args, **_kwargs):
        raise AssertionError("recovery must not replace any existing workbench slots")

    coordinator._restore_plugin_sources = forbidden_restore
    coordinator._start_current_project_open(
        lambda: OperationResult.completed(object()),
        dirty_decision=None,
        success_verb="已打开",
        on_success=successes.append,
    )
    deadline = time.monotonic() + 3
    while host.project_open_worker is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert host.project_open_worker is None
    assert not successes
    assert progress == ["shown", "hidden"]
    assert "只读恢复" in messages[-1]
    assert coordinator._recovery_dialog.table.model().rowCount() == 2
    coordinator._recovery_dialog.close()
    host.close()
