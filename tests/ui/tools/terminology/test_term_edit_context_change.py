import threading
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox
import pytest

from tests.ui.tools.terminology.test_context_isolation import _Context
from tests.ui.tools.terminology.test_context_save_switching import _wait
from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.terminology.launcher import TerminologyLauncher
from transbridge.ui.tools.terminology.term_edit_dialog import TermEditDialog

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def editor(monkeypatch):
    context = _Context()
    launcher = TerminologyLauncher(
        SimpleNamespace(app_runtime=SimpleNamespace(), runtime_context=RequestContext("operator"), context=context)
    )
    window = launcher.open()
    dialog = TermEditDialog(parent=window)
    dialog.original.setText("Dragon")
    dialog.translation.setText("未保存的译名")
    dialog.notes.setPlainText("请保留这段备注")
    dialog.show()
    try:
        yield context, launcher, window, dialog
    finally:
        QThreadPool.globalInstance().waitForDone(5000)
        _APP.processEvents()
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        if not sip.isdeleted(dialog):
            dialog.close()
        launcher.close()
        _APP.processEvents()


@pytest.mark.parametrize("fail", [False, True])
def test_context_switch_during_editor_save_keeps_failed_inputs_and_finishes_success(editor, monkeypatch, fail):
    context, launcher, window, dialog = editor
    started, release = threading.Event(), threading.Event()
    saved = []
    original_presenter = window.presenter

    def write():
        started.set()
        assert release.wait(5)
        if fail:
            raise RuntimeError("原版本写入失败")
        saved.append("saved")

    dialog.save_requested.connect(
        lambda _values: window._run_draft_command(write, on_saved=dialog.saved, on_error=dialog.save_failed)
    )
    dialog.save_button.click()
    _wait(started.is_set)
    try:
        context.switch(("project-b", "variant-b"))
        assert dialog.has_unsaved_inputs
        assert dialog.original.isReadOnly()
        assert dialog.notes.isReadOnly()
        release.set()
        _wait(lambda: window._draft_writes.pending_count == 0)
        if not fail:
            _wait(lambda: original_presenter.closed)
            assert saved == ["saved"]
            assert not dialog.isVisible()
            assert not dialog.has_unsaved_inputs
            return

        assert dialog.isVisible()
        assert dialog.translation.text() == "未保存的译名"
        assert "原版本写入失败" in dialog.error.text()
        assert "切回原版本" in dialog.error.text()
        assert not dialog.save_button.isEnabled()
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).isEnabled()
        dialog.save_button.click()
        dialog._save()  # A queued request must also remain harmless after invalidation.
        assert not dialog._saving
        assert window._draft_writes.pending_count == 0
        dialog.translation.selectAll()
        dialog.translation.copy()
        assert _APP.clipboard().text() == "未保存的译名"

        current = launcher.open()
        assert current is not window and current.context_is_current
        assert "原版本写入失败" in dialog.error.text()
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
        assert not window.close()
        assert dialog.isVisible()
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
        assert window.close()
        assert original_presenter.closed
    finally:
        release.set()


def test_context_switch_keeps_unsaved_editor_visible_until_explicit_discard(editor, monkeypatch):
    context, launcher, window, dialog = editor

    context.switch(("project-b", "variant-b"))

    assert dialog.isVisible() and dialog.has_unsaved_inputs
    assert not window.presenter.closed
    assert not window.workspace.isEnabled()
    assert dialog.translation.isReadOnly()
    assert not dialog.save_button.isEnabled()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text() == "关闭"
    dialog.notes.selectAll()
    dialog.notes.copy()
    assert _APP.clipboard().text() == "请保留这段备注"
    assert launcher.open() is not window
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    assert not window.close()
    assert dialog.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    assert window.close()


def test_explicit_discard_notifies_a_queued_editor_and_finishes_the_running_write(editor, monkeypatch):
    _context, _launcher, window, dialog = editor
    started, release = threading.Event(), threading.Event()
    writes, confirmations = [], []
    presenter = window.presenter

    def first():
        started.set()
        assert release.wait(5)
        writes.append("first")

    monkeypatch.setattr("transbridge.ui.tools.terminology.save_close.choose_pending_save_action", lambda *_: "discard")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: confirmations.append(args) or QMessageBox.StandardButton.Yes
    )
    window._run_draft_command(first)
    _wait(started.is_set)
    dialog.save_requested.connect(
        lambda _values: window._run_draft_command(
            lambda: writes.append("discarded"), on_saved=dialog.saved, on_error=dialog.save_failed
        )
    )
    try:
        dialog.save_button.click()
        assert dialog._saving and window._draft_writes.pending_count == 2
        assert not window.close()
        assert not dialog._saving and not dialog.isVisible()
        assert window._draft_writes.pending_count == 1
        release.set()
        _wait(lambda: presenter.closed)
        assert writes == ["first"]
        assert window._draft_writes.last_error is None
        assert window._draft_writes.failure_count == 0
        assert not confirmations
    finally:
        release.set()
