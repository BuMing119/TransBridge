"""Conflict choices stay visible until their draft write has actually succeeded."""

import threading
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialogButtonBox, QMessageBox, QWidget
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.terminology.test_context_isolation import _Context
from tests.ui.tools.terminology.test_context_save_switching import _wait
from tests.ui.tools.terminology.test_draft_interactions import _details
from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.terminology.conflict_review_dialog import ConflictReviewDialog
from transbridge.ui.tools.terminology.draft_interactions import DraftInteractionController
from transbridge.ui.tools.terminology.draft_write_queue import DraftWriteDiscarded
from transbridge.ui.tools.terminology.launcher import TerminologyLauncher


def test_failed_conflict_write_preserves_choice_for_retry_until_success(layout_app, monkeypatch):
    opened, pending = [], []
    parent = QWidget()

    def show_and_submit(dialog):
        opened.append(dialog)
        dialog.show()
        settle_layout(layout_app)
        dialog.operation.setCurrentIndex(1)
        dialog.plugin.setCurrentText("Other.esp")
        dialog.translation.setCurrentText("My reviewed translation")
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
        return dialog.result()

    def queue(command, on_saved=None, on_error=None):
        pending.append((command, on_saved, on_error))

    monkeypatch.setattr(ConflictReviewDialog, "exec", show_and_submit)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    details = _details()
    presenter = SimpleNamespace(
        conflict_details=lambda _item: details, resolve_review=lambda *_args, **_kwargs: "saved"
    )
    controller = DraftInteractionController(parent, presenter, queue, lambda call, ready, _message: ready(call()))
    try:
        controller.review(object())
        dialog = opened[0]
        assert dialog.isVisible(), "The review must remain open while the draft write is pending"
        assert len(pending) == 1
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.isVisible()
        pending[0][2](RuntimeError("synthetic write failure"))
        assert "synthetic write failure" in dialog.error.text()
        assert dialog.translation.currentText() == "My reviewed translation"
        assert dialog.plugin.currentText() == "Other.esp"
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        assert len(pending) == 2
        pending[1][1](pending[1][0]())
        assert not dialog.isVisible()
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        for dialog in opened:
            dialog.close()
        parent.close()


def test_escape_does_not_silently_discard_a_typed_conflict_choice(layout_app, monkeypatch):
    dialog = ConflictReviewDialog(_details())
    answers = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: answers.append(True) or QMessageBox.StandardButton.No)
    dialog.show()
    settle_layout(layout_app)
    dialog.translation.setCurrentText("Unsubmitted choice")
    try:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.isVisible()
        assert dialog.translation.currentText() == "Unsubmitted choice"
        assert answers == [True]
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        dialog.close()


def test_explicit_queued_discard_closes_review_without_a_second_prompt(layout_app, monkeypatch):
    dialog = ConflictReviewDialog(_details())
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: prompts.append(True))
    dialog.show()
    dialog.translation.setCurrentText("Queued choice")
    dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert dialog._saving
    dialog.save_failed(DraftWriteDiscarded("explicitly discarded"))
    assert not dialog.isVisible()
    assert not prompts


@pytest.mark.parametrize("pending", [False, True])
def test_context_switch_keeps_review_inputs_and_parent_close_respects_discard(layout_app, monkeypatch, pending):
    context = _Context()
    launcher = TerminologyLauncher(
        SimpleNamespace(app_runtime=SimpleNamespace(), runtime_context=RequestContext("operator"), context=context)
    )
    window = launcher.open()
    dialog = ConflictReviewDialog(_details(), window)
    started, release = threading.Event(), threading.Event()

    def fail():
        started.set()
        assert release.wait(5)
        raise RuntimeError("Original draft write failed")

    dialog.save_requested.connect(
        lambda _choice: window._run_draft_command(fail, on_saved=dialog.saved, on_error=dialog.save_failed)
    )
    dialog.translation.setCurrentText("Unsaved reviewed name")
    dialog.show()
    settle_layout(layout_app)
    try:
        if pending:
            dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
            _wait(started.is_set)
        context.switch(("project-b", "variant-b"))
        assert dialog.isVisible() and dialog.has_unsaved_inputs
        assert not window.presenter.closed
        assert dialog.translation.lineEdit().isReadOnly()
        if pending:
            assert not window.close()
            release.set()
            _wait(lambda: window._draft_writes.pending_count == 0)
            assert "Original draft write failed" in dialog.error.text()
        assert "切回原版本" in dialog.error.text()
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).isEnabled()
        dialog._save()
        assert window._draft_writes.pending_count == 0
        assert dialog.translation.currentText() == "Unsaved reviewed name"
        dialog.translation.lineEdit().selectAll()
        dialog.translation.lineEdit().copy()
        assert layout_app.clipboard().text() == "Unsaved reviewed name"
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
        assert not window.close()
        assert dialog.isVisible()
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        assert window.close()
        assert not dialog.isVisible()
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(5000)
        layout_app.processEvents()
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        if not sip.isdeleted(dialog):
            dialog.close()
        launcher.close()
        layout_app.processEvents()
