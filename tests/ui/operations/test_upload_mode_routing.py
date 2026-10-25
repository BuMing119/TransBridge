from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton
import pytest

from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.operations import categorized_upload_bridge
from transbridge.ui.operations.upload_mode_dialog import UploadModeDialog
from transbridge.ui.workbench.cards import upload_views
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


def test_plain_batch_keeps_plugin_selection_and_translation_settings(qapp, monkeypatch):
    calls = []
    slots = {name: SimpleNamespace(label=name) for name in ("a", "b")}
    context = SimpleNamespace(collection=[object()], slots=slots)
    project = {"id": 42, "name": "Remote"}
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _ctx: project)
    monkeypatch.setattr(UploadModeDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    def select(dialog):
        dialog._checkboxes["b"].setChecked(False)
        return QDialog.DialogCode.Accepted

    def configure(dialog):
        dialog._rb_trans_only.setChecked(True)
        dialog._rb_trans_force.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(upload_views.SlotSelectDialog, "exec", select)
    monkeypatch.setattr(upload_views.BatchUploadModeDialog, "exec", configure)
    card = UploadCard(context, lambda **_kwargs: pytest.fail("Unexpected worker"))
    card.bind_operation_plan_facade(SimpleNamespace(supports=lambda *_args, **_kwargs: False))
    card.do_batch_upload = lambda selected, project, mode: calls.append((selected, project, mode))

    card.batch_upload()

    assert calls == [([slots["a"]], project, "trans_force")]
    card.close()


@pytest.mark.parametrize("batch", [False, True])
def test_category_launch_captures_current_source_only_for_single_upload(qapp, monkeypatch, batch):
    captured = []
    namespace = object()
    context = SimpleNamespace(
        collection=[SimpleNamespace(identity=SimpleNamespace(namespace=namespace))],
        project_name="Project",
        active_variant="Variant",
    )

    def session(_context, *, source_namespace, categorized):
        assert categorized
        captured.append(source_namespace)
        return SimpleNamespace(target=SimpleNamespace(project_name="Remote"), load_preview=object(), upload=object())

    monkeypatch.setattr(categorized_upload_bridge, "CategorizedUploadSession", session)
    monkeypatch.setattr(
        categorized_upload_bridge,
        "CategorizedUploadDialog",
        lambda *_args, **kwargs: SimpleNamespace(show=lambda: captured.append(kwargs["batch"])),
    )

    categorized_upload_bridge.open_categorized_upload(context, batch=batch)

    assert captured == [None if batch else namespace, batch]
