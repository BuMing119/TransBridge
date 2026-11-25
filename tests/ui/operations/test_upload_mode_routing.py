from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox
import pytest

from transbridge.paratranz.project_policy import PolicyAccess
from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.operations import categorized_upload_bridge
from transbridge.ui.operations.upload_mode_dialog import UploadModeDialog
from transbridge.ui.workbench.cards.upload_card import UploadCard


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("entrypoint", ["card", "menu"])
@pytest.mark.parametrize("mode", ["plain", "categorized", "cancel"])
def test_upload_entry_asks_for_mode_before_opening_the_file_window(qapp, monkeypatch, entrypoint, mode):
    choices = []
    sessions = []
    dialogs = []
    context = SimpleNamespace(
        collection=[object()], slots={"a": object(), "b": object()}, project_name="Project", active_variant="Variant"
    )
    session = SimpleNamespace(
        target=SimpleNamespace(project_name="Remote"),
        load_source_catalog=object(),
        load_preview=object(),
        upload=object(),
        retry_upload=object(),
        check_policy_target=lambda: None,
    )

    def create_session(owner, *, categorized, select_sources):
        sessions.append((owner, categorized, select_sources))
        return session

    def open_dialog(project, version, target, **options):
        dialogs.append((project, version, target, options))
        return SimpleNamespace(show=lambda: None)

    def select_mode():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, UploadModeDialog):
            return
        choices.append((dialog.categorized, len(sessions)))
        buttons = dialog.findChild(QDialogButtonBox)
        if mode == "cancel":
            buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        else:
            if mode == "categorized":
                dialog._categorized.click()
            buttons.button(QDialogButtonBox.StandardButton.Ok).click()

    monkeypatch.setattr(categorized_upload_bridge, "CategorizedUploadSession", create_session)
    monkeypatch.setattr(categorized_upload_bridge, "initial_policy", lambda *_: PolicyAccess(None, None, 1, None, ()))
    monkeypatch.setattr(categorized_upload_bridge, "CategorizedUploadDialog", open_dialog)
    facade = SimpleNamespace(begin_upload=categorized_upload_bridge.open_file_upload)
    card = UploadCard(context, lambda **_kwargs: pytest.fail("Unexpected legacy upload"))
    card.bind_operation_plan_facade(facade)
    host = SimpleNamespace(context=context, upload_card=card)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(select_mode)
    timer.start(0)
    try:
        if entrypoint == "menu":
            OperationCoordinator(host).upload()
        else:
            card.btn.click()
        assert choices == [(False, 1)]
        assert sessions == [(context, False, True)]
        if mode == "cancel":
            assert dialogs == []
        else:
            categorized = mode == "categorized"
            assert session.categorized == categorized
            [(project, version, target, options)] = dialogs
            assert (project, version, target) == ("Project", "Variant", "Remote")
            assert options["categorized"] is categorized
            assert options["load_catalog"] is session.load_source_catalog
            assert options["upload"] is session.upload
        assert card.btn.text() == "上传…"
        assert card.batch_btn.isHidden()
    finally:
        timer.stop()
        card.close()


def test_missing_upload_facade_reports_unavailable_without_starting_another_flow(qapp, monkeypatch):
    context = SimpleNamespace(collection=[object()], slots={"a": object(), "b": object()})
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append(_args[1]))
    card = UploadCard(context, lambda **_kwargs: pytest.fail("A missing facade must not start another upload"))
    try:
        card.btn.click()
        assert messages == ["上传入口不可用"]
    finally:
        card.close()
