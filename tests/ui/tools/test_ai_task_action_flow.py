from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox, QPushButton
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app  # noqa: F401
from tests.ui.tools import test_unified_task_progress as fixtures
from tests.ui.tools.test_unified_task_progress import _start, _wait_reports, _wait_until
from transbridge.ui.tools.ai_translator._polish_preview_dialog import _PolishPreviewDialog
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_result_dialog import TaskResultDialog

make_window = fixtures.make_window
qapp = fixtures.qapp


def _click(button):
    assert button.isVisible() and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())


def _primary(window):
    return [
        button
        for button in window.findChildren(QPushButton)
        if button.isVisibleTo(window) and button.property("tbTaskPrimary")
    ]


def _dismiss_notice(window):
    notices = [dialog for dialog in window._dialogs if isinstance(dialog, QMessageBox) and dialog.isVisible()]
    for notice in notices:
        _click(next(button for button in notice.buttons() if button.text() == "关闭提示"))
    return notices


def _schedule_preview_decision(monkeypatch, *, accept):
    original_exec = _PolishPreviewDialog.exec
    visited = []

    def exec_with_clicks(dialog):
        def choose():
            if dialog._loading:
                QTimer.singleShot(10, choose)
                return
            visited.append(dialog)
            if accept:
                _click(dialog._accept_all_btn)
                _click(dialog._apply_btn)
            else:
                _click(next(button for button in dialog.findChildren(QPushButton) if button.text() == "取消"))

        QTimer.singleShot(0, choose)
        return original_exec(dialog)

    monkeypatch.setattr(_PolishPreviewDialog, "exec", exec_with_clicks)
    return visited


def test_running_actions_logs_pause_and_cancel_follow_worker_lifecycle(layout_app, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    window.show()
    settle_layout(layout_app)
    assert _primary(window) == [window.pause_button]
    assert not window.preview_button.isVisible()
    assert not window.retry_button.isVisible()
    assert not window.save_button.isVisible()
    assert not window.report_button.isVisible()
    assert window.logs.isVisible()
    worker.log.emit("first", "开始翻译，共 2 条")
    _click(window.log_toggle)
    assert not window.logs.isVisible() and "开始翻译" in window.logs.toPlainText()
    _click(window.log_toggle)
    assert window.logs.isVisible() and "开始翻译" in window.logs.toPlainText()
    _click(window.pause_button)
    assert worker.is_paused and "继续处理" in window.next_step.text()
    worker.pause_state_changed.emit("paused")
    _click(window.pause_button)
    assert not worker.is_paused and window.pause_button.text() == "暂停"
    _click(window.stop_button)
    assert worker.was_cancelled and window.run.worker is worker
    assert "等待取消完成" in window.next_step.text()
    assert not window.stop_button.isEnabled()
    assert _primary(window) == []
    worker.publish(())
    _wait_reports(layout_app, window)
    assert window.stop_button.text() == "关闭" and not session.commits
    _click(window.stop_button)
    assert not window.isVisible()


def test_pending_confirmation_clicks_preview_then_completion_and_read_only_result(layout_app, make_window, monkeypatch):
    window, session, _ = make_window(polish=True, preview=True)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    assert not session.commits
    assert _primary(window) == [window.preview_button]
    assert "确认结果" in window.next_step.text()
    visited = _schedule_preview_decision(monkeypatch, accept=True)
    _click(window.preview_button)
    _wait_reports(layout_app, window)
    assert len(visited) == 2 and session.commits == 1 and session.saved
    notices = _dismiss_notice(window)
    assert len(notices) == 1 and notices[0].defaultButton().text() == "查看结果"
    assert window.isVisible() and not window.run.cancelled
    assert _primary(window) == [window.report_button]
    assert not window.pause_button.isVisible() and not window.save_button.isVisible()
    assert not window.retry_button.isVisible() and not window.preview_button.isVisible()
    _click(window.report_button)
    result = window._dialogs[-1]
    assert isinstance(result, TaskResultDialog) and result.isVisible()
    _wait_until(lambda: not result._projecting)
    assert result.record["applied"] and result.model.rowCount() == 1
    result.close()
    _click(window.stop_button)
    assert session.commits == 1 and session.saved and not window.run.cancelled


def test_declining_confirmation_keeps_existing_cancel_boundary(layout_app, make_window, monkeypatch):
    window, session, _ = make_window(polish=True, preview=True)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    visited = _schedule_preview_decision(monkeypatch, accept=False)
    _click(window.preview_button)
    _wait_reports(layout_app, window)
    assert len(visited) == 1 and not session.commits and session.discarded
    assert window.run.cancelled and "任务已取消" in window.next_step.text()
    assert not window.preview_button.isVisible() and not window.retry_button.isVisible()


def test_partial_failure_retries_only_failed_entries_then_offers_result(layout_app, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    first, second = session.tasks
    worker.publish((SourceOutcome(first), SourceOutcome(second, error="provider unavailable")))
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    notices = _dismiss_notice(window)
    assert notices[0].defaultButton().text() == "重试失败条目"
    assert _primary(window) == [window.retry_button]
    assert "已应用的结果会保留" in window.next_step.text()
    applied = set(session.applied_keys)
    _click(window.retry_button)
    _wait_until(lambda: not window.run.preparing)
    retry_worker = window.run.worker
    assert [task.key for task in retry_worker.tasks] == ["second"]
    assert session.applied_keys == applied
    assert _primary(window) == [window.pause_button]
    retry_worker.publish(tuple(SourceOutcome(task) for task in retry_worker.tasks))
    _wait_reports(layout_app, window)
    _dismiss_notice(window)
    assert session.commits == 2 and _primary(window) == [window.report_button]


@pytest.mark.parametrize("snapshot", [False, True])
def test_save_failure_prioritizes_save_without_rerunning_or_reapplying(layout_app, make_window, monkeypatch, snapshot):
    window, session, _ = make_window()
    save = session.save_translation

    def fail_save(*, on_success, on_error):
        session.project_saved = snapshot
        on_error("test storage unavailable")

    monkeypatch.setattr(session, "save_translation", fail_save)
    worker = _start(window, session)
    first, second = session.tasks
    worker.publish((SourceOutcome(first), SourceOutcome(second, error="provider unavailable")))
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    notices = _dismiss_notice(window)
    assert notices[0].defaultButton().text() == ("重试快照" if snapshot else "重试保存")
    assert _primary(window) == [window.save_button]
    assert "无需重新运行任务" in window.next_step.text()
    assert window.retry_button.isVisible() and window.retry_button.isEnabled()
    assert session.commits == 1 and not session.saved
    monkeypatch.setattr(session, "save_translation", save)
    _click(window.save_button)
    _wait_reports(layout_app, window)
    _dismiss_notice(window)
    assert session.saved and session.commits == 1 and window.run.worker is None
    assert _primary(window) == [window.retry_button]


def test_failed_application_offers_existing_success_without_rerunning(layout_app, make_window, monkeypatch):
    window, session, _ = make_window()
    apply = session.apply_entries_async
    monkeypatch.setattr(session, "apply_entries_async", lambda keys, **callbacks: callbacks["on_error"]("write failed"))
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    assert _primary(window) == [window.preview_button]
    assert window.preview_button.text() == "应用成功结果" and not session.commits
    monkeypatch.setattr(session, "apply_entries_async", apply)
    _click(window.preview_button)
    _wait_reports(layout_app, window)
    _dismiss_notice(window)
    assert session.commits == 1 and session.saved and window.run.worker is None
    assert _primary(window) == [window.report_button]


def test_preparation_failure_explains_new_task_path_and_close_is_not_cancel(layout_app, make_window):
    window, session, _ = make_window()
    window.prepare()
    session.is_busy = False
    session.error_callback("snapshot unavailable")
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    assert _primary(window) == [window.stop_button]
    assert "重新创建任务" in window.next_step.text()
    assert not window.retry_button.isVisible() and not window.save_button.isVisible()
    _click(window.stop_button)
    assert not window.isVisible() and not window.run.cancelled and not session.commits


@pytest.mark.parametrize("phase", ["apply", "save"])
def test_closing_during_application_or_save_does_not_interrupt_completion(layout_app, make_window, monkeypatch, phase):
    window, session, _ = make_window()
    operation = session.apply_entries_async if phase == "apply" else session.save_translation
    pending = []

    def delayed(*args, **callbacks):
        session.is_busy = True
        pending.append((args, callbacks))

    monkeypatch.setattr(session, "apply_entries_async" if phase == "apply" else "save_translation", delayed)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    window.show()
    settle_layout(layout_app)
    assert _primary(window) == []
    assert "关闭窗口不会中断" in window.next_step.text()
    assert window.stop_button.text() == "关闭"
    _click(window.stop_button)
    assert session.is_busy and not window.run.cancelled
    session.is_busy = False
    args, callbacks = pending.pop()
    operation(*args, **callbacks)
    _wait_reports(layout_app, window)
    _dismiss_notice(window)
    window.show()
    settle_layout(layout_app)
    assert session.saved and session.commits == 1
    assert _primary(window) == [window.report_button]


def test_record_failure_offers_record_retry_without_rerunning_completed_task(layout_app, make_window, monkeypatch):
    window, session, _ = make_window()
    save = fixtures.TaskHistoryStore.save

    def fail_record(*args):
        raise OSError("test history unavailable")

    monkeypatch.setattr(fixtures.TaskHistoryStore, "save", fail_record)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(layout_app, window)
    window.show()
    settle_layout(layout_app)
    notices = _dismiss_notice(window)
    assert notices[0].defaultButton().text() == "重试保存任务记录"
    assert _primary(window) == [window.retry_record_button]
    assert window.report_button.isVisible() and window.report_button.isEnabled()
    assert "无需重新运行任务" in window.next_step.text()
    monkeypatch.setattr(fixtures.TaskHistoryStore, "save", save)
    _click(window.retry_record_button)
    _wait_reports(layout_app, window)
    assert not window.run.records.error and session.commits == 1 and session.saved
    assert _primary(window) == [window.report_button]


def test_actions_and_next_step_fit_narrow_window_with_large_text(layout_app, make_window, monkeypatch):
    window, session, _ = make_window()
    font = QFont(window.font())
    font.setPointSize(12)
    window.setFont(font)
    monkeypatch.setattr(session, "save_translation", lambda **callbacks: callbacks["on_error"]("test save unavailable"))
    worker = _start(window, session)
    first, second = session.tasks
    worker.publish((SourceOutcome(first), SourceOutcome(second, error="provider unavailable")))
    _wait_reports(layout_app, window)
    _dismiss_notice(window)
    window.resize(720, 680)
    window.show()
    settle_layout(layout_app)
    assert window.width() == 720
    actions = [window.retry_button, window.save_button, window.report_button, window.stop_button]
    assert all(button.isVisible() and window.rect().contains(button.geometry()) for button in actions)
    assert all(not a.geometry().intersects(b.geometry()) for a, b in zip(actions, actions[1:]))
    assert window.next_step.height() >= window.next_step.heightForWidth(window.next_step.width())
    assert _primary(window) == [window.save_button]
