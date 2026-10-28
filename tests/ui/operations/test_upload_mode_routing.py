from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton
import pytest

from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.operations import categorized_upload_bridge
from transbridge.ui.operations.upload_mode_dialog import UploadModeDialog
from transbridge.ui.workbench.cards.upload_card import UploadCard


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("entrypoint", ["card", "menu"])
@pytest.mark.parametrize("mode", ["plain", "categorized", "cancel"])
def test_upload_mode_is_chosen_once_for_both_entrypoints_and_scopes(qapp, monkeypatch, batch, entrypoint, mode):
    calls = []
    context = SimpleNamespace(collection=[object()], slots={"a": object(), "b": object()})
    facade = SimpleNamespace(begin_upload=lambda context, batch=False: calls.append(("plain", context, batch)))
    monkeypatch.setattr(
        categorized_upload_bridge,
        "open_categorized_upload",
        lambda context, parent=None, batch=False: calls.append(("categorized", context, batch)),
    )
    card = UploadCard(context, lambda **_kwargs: pytest.fail("Unexpected legacy upload"))
    card.bind_operation_plan_facade(facade)
    host = SimpleNamespace(context=context, operation_plan_facade=facade, upload_card=card)

    def choose():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, UploadModeDialog)
        assert ("批量" in dialog.windowTitle()) == batch
        assert dialog.mode == "plain"
        if mode == "cancel":
            dialog.reject()
        else:
            if mode == "categorized":
                dialog._categorized.click()
            dialog.accept()

    QTimer.singleShot(0, choose)
    if entrypoint == "menu":
        coordinator = OperationCoordinator(host)
        coordinator.batch_upload() if batch else coordinator.upload()
    else:
        card.batch_btn.click() if batch else card.btn.click()

    assert calls == ([] if mode == "cancel" else [(mode, context, batch)])
    buttons = card.findChildren(QPushButton, options=Qt.FindChildOption.FindDirectChildrenOnly)
    assert {button.text() for button in buttons} == {"上传", "批量"}
    card.close()
    card.deleteLater()


@pytest.mark.parametrize("batch", [False, True])
def test_category_launch_captures_current_source_only_for_single_upload(qapp, monkeypatch, batch):
    captured = []
    namespace = object()
    context = SimpleNamespace(
        collection=[SimpleNamespace(identity=SimpleNamespace(namespace=namespace))],
        uses_authoritative_projection=True,
        project_name="Project",
        active_variant="Variant",
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: pytest.fail("Unexpected upload prerequisite warning"))

    def session(_context, *, source_namespace, categorized):
        assert categorized
        captured.append(source_namespace)
        return SimpleNamespace(
            target=SimpleNamespace(project_name="Remote"), load_preview=object(), upload=object(), retry_upload=object()
        )

    monkeypatch.setattr(categorized_upload_bridge, "CategorizedUploadSession", session)
    monkeypatch.setattr(
        categorized_upload_bridge,
        "CategorizedUploadDialog",
        lambda *_args, **kwargs: SimpleNamespace(
            show=lambda: captured.append((kwargs["batch"], kwargs["retry_upload"]))
        ),
    )

    categorized_upload_bridge.open_categorized_upload(context, batch=batch)

    assert captured[0] == (None if batch else namespace)
    assert captured[1][0] == batch and captured[1][1] is not None


@pytest.mark.parametrize("batch", [False, True])
def test_missing_upload_facade_reports_unavailable_without_starting_another_flow(qapp, monkeypatch, batch):
    context = SimpleNamespace(collection=[object()], slots={"a": object(), "b": object()})
    messages = []
    monkeypatch.setattr(UploadModeDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append(_args[1]))
    card = UploadCard(context, lambda **_kwargs: pytest.fail("A missing facade must not start another upload"))
    try:
        card.batch_upload() if batch else card.upload()
        assert messages == ["上传入口不可用"]
    finally:
        card.close()
