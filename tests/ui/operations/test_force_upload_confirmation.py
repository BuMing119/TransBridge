"""Force uploads require explicit consent before any worker or retry starts."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from tests.ui.operations import test_categorized_upload_dialog as view
from transbridge.ui.operations.categorized_upload_dialog import UploadReceipt

qapp = view.qapp
worker_queue = view.worker_queue


def _answer(action, observed, subject):
    def respond():
        box = QApplication.activeModalWidget()
        observed.append((
            box.windowTitle(),
            box.text() + box.informativeText(),
            box.defaultButton() == box.button(QMessageBox.StandardButton.Cancel),
            box.escapeButton() == box.button(QMessageBox.StandardButton.Cancel),
            subject._worker,
        ))
        if action == "escape":
            QTest.keyClick(box, Qt.Key.Key_Escape)
        elif action == "enter":
            QTest.keyClick(box, Qt.Key.Key_Return)
        elif action == "close":
            box.close()
        else:
            button = QMessageBox.StandardButton.Yes if action == "confirm" else QMessageBox.StandardButton.Cancel
            box.button(button).click()

    QTimer.singleShot(0, respond)


@pytest.mark.parametrize("categorized", [False, True])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("action", ["confirm", "cancel", "escape", "enter", "close"])
def test_force_upload_confirms_at_execution_for_all_layouts_and_scopes(qapp, worker_queue, categorized, batch, action):
    uploaded, observed = [], []
    subject = view._dialog(
        categorized=categorized,
        batch=batch,
        upload=lambda selection, *_args: uploaded.append(selection) or UploadReceipt(2, 0),
    )
    subject.show()
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        subject._mode.setCurrentIndex(subject._mode.findData("trans_force"))
        before = subject.selection()
        for _ in range(2):
            workers_before = len(worker_queue)
            _answer(action, observed, subject)
            subject._upload_button.click()
            assert subject.selection() == before
            if action == "confirm":
                assert len(worker_queue) == workers_before + 1
                worker_queue[-1].complete()
                assert uploaded[-1] == before
            else:
                assert len(worker_queue) == workers_before
                assert uploaded == []
                assert subject._retry_count == 0
            assert subject._worker is None
            assert subject._upload_button.isEnabled()
        assert len(observed) == 2
        for title, text, default_cancel, escape_cancel, worker in observed:
            assert title == "确认强制更新译文"
            assert "人工译文" in text and "空译文也会覆盖已有译文" in text and "将被清空" in text
            assert default_cancel and escape_cancel and worker is None
    finally:
        subject.close()


@pytest.mark.parametrize("mode", ["orig_only", "trans_safe", "both"])
def test_nonforce_upload_and_retry_do_not_prompt(qapp, worker_queue, monkeypatch, mode):
    monkeypatch.setattr(QMessageBox, "exec", lambda *_args: pytest.fail("Unexpected force confirmation"))
    uploaded, retried = [], []
    subject = view._dialog(
        upload=lambda selection, *_args: uploaded.append(selection) or UploadReceipt(1, 1, retry_count=1),
        retry_upload=lambda *_args: retried.append(True) or UploadReceipt(1, 0),
    )
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        subject._mode.setCurrentIndex(subject._mode.findData(mode))
        subject._upload_button.click()
        worker_queue[-1].complete()
        subject._mode.setCurrentIndex(subject._mode.findData("trans_force"))
        subject._retry_button.click()
        worker_queue[-1].complete()
        assert uploaded[0].translation_mode == mode
        assert retried == [True]
    finally:
        subject.close()


@pytest.mark.parametrize("categorized", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_force_retry_uses_original_mode_and_cancel_preserves_pending_work(qapp, worker_queue, categorized, batch):
    observed, retried = [], []
    subject = view._dialog(
        categorized=categorized,
        batch=batch,
        upload=lambda *_args: UploadReceipt(1, 1, retry_count=1),
        retry_upload=lambda *_args: retried.append(True) or UploadReceipt(1, 0),
    )
    subject.show()
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        subject._mode.setCurrentIndex(subject._mode.findData("trans_force"))
        _answer("confirm", observed, subject)
        subject._upload_button.click()
        worker_queue[-1].complete()
        subject._mode.setCurrentIndex(subject._mode.findData("trans_safe"))
        for action in ("cancel", "confirm"):
            workers_before = len(worker_queue)
            _answer(action, observed, subject)
            subject._retry_button.click()
            if action == "cancel":
                assert len(worker_queue) == workers_before
                assert retried == []
                assert subject._retry_count == 1
                assert subject._retry_button.isEnabled()
            else:
                assert len(worker_queue) == workers_before + 1
                worker_queue[-1].complete()
                assert retried == [True]
                assert subject._retry_count == 0
        assert len(observed) == 3
    finally:
        subject.close()


def test_cancelling_new_force_upload_keeps_previous_safe_retry(qapp, worker_queue, monkeypatch):
    observed, retried = [], []
    subject = view._dialog(
        upload=lambda *_args: UploadReceipt(1, 1, retry_count=1),
        retry_upload=lambda *_args: retried.append(True) or UploadReceipt(1, 0),
    )
    subject.show()
    qapp.processEvents()
    worker_queue[-1].complete()
    try:
        subject._mode.setCurrentIndex(subject._mode.findData("trans_safe"))
        subject._upload_button.click()
        worker_queue[-1].complete()
        subject._mode.setCurrentIndex(subject._mode.findData("trans_force"))
        workers_before = len(worker_queue)
        _answer("cancel", observed, subject)
        subject._upload_button.click()
        assert len(worker_queue) == workers_before
        assert subject._retry_count == 1
        monkeypatch.setattr(QMessageBox, "exec", lambda *_args: pytest.fail("Previous safe retry must not prompt"))
        subject._retry_button.click()
        worker_queue[-1].complete()
        assert retried == [True]
    finally:
        subject.close()
