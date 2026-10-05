from unittest.mock import Mock

from PyQt6.QtWidgets import QMessageBox

from tests.ui.tools import test_unified_task_progress as support
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome

qapp = support.qapp
make_window = support.make_window


def test_completion_notice_waits_for_save_and_is_not_duplicated(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    notices, callbacks = [], {}
    window.run.completion_notice.connect(notices.append)

    def save(**handlers):
        session.is_busy = True
        callbacks.update(handlers)

    monkeypatch.setattr(session, "save_translation", save)
    worker = support._start(window, session)
    outcomes = tuple(SourceOutcome(task) for task in session.tasks)
    worker.publish(outcomes)
    qapp.processEvents()
    assert not notices and session.is_busy
    assert "正在保存" in window.save_status.text()
    session.saved = session.project_saved = session.snapshot_saved = True
    session.is_busy = False
    callbacks["on_success"]({})
    support._wait_reports(qapp, window)
    assert len(notices) == 1 and "项目已保存" in notices[0]["title"]
    worker.completed.emit(outcomes)
    window.run.completion.notify()
    support._wait_reports(qapp, window)
    assert len(notices) == 1


def test_retry_discards_previous_attempt_pending_save_error(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    notices = []
    window.run.completion_notice.connect(notices.append)
    monkeypatch.setattr(session, "save_translation", lambda **handlers: handlers["on_error"]("disk full"))
    worker = support._start(window, session)
    worker.publish((SourceOutcome(session.tasks[0]), SourceOutcome(session.tasks[1], error="network")))
    assert window.run.completion.pending is not None
    window.run.retry()
    assert window.run.completion.pending is None
    assert not window.run.completion._timer.isActive()
    assert not notices


def test_running_log_opens_before_completion_and_retry_cannot_open_old_log(qapp, make_window, monkeypatch, tmp_path):
    from transbridge.ui.tools.ai_translator import _llm_log_viewer

    window, session, _ = make_window()
    worker = support._start(window, session)
    log_dir = tmp_path / "attempt-one"
    log_dir.mkdir()
    viewer = Mock(wraps=_llm_log_viewer._LLMLogViewer)
    monkeypatch.setattr(_llm_log_viewer, "_LLMLogViewer", viewer)
    worker.source_started.emit("first")
    worker.log_ready.emit("first", str(log_dir))
    window.log_button.click()
    viewer.assert_called_once_with(str(log_dir))
    assert not window.run.completion_received
    worker.publish(tuple(SourceOutcome(task, error="request failed", log_dir=str(log_dir)) for task in session.tasks))
    support._wait_reports(qapp, window)
    window.run.retry()
    window.log_button.click()
    assert viewer.call_count == 1
    assert isinstance(window._dialogs[-1], QMessageBox)
    assert "等待日志" in window._dialogs[-1].text()
    worker.log_ready.emit("first", "stale-path")
    assert window.run.log_paths["first"] is None


def test_completion_dialog_offers_retry_for_failed_entries(qapp, make_window):
    window, session, _ = make_window()
    worker = support._start(window, session)
    worker.publish((SourceOutcome(session.tasks[0]), SourceOutcome(session.tasks[1], error="request failed")))
    support._wait_reports(qapp, window)
    dialog = window._dialogs[-1]
    assert isinstance(dialog, QMessageBox)
    assert "成功结果已保存" in dialog.windowTitle()
    assert {button.text() for button in dialog.buttons()} == {"查看结果", "重试失败条目", "关闭"}
