from __future__ import annotations

import threading

from PyQt6.QtCore import pyqtSignal

from tests.ui.tools import test_unified_task_progress as support
from transbridge.ui.tools.ai_translator import task_run

make_window = support.make_window
qapp = support.qapp


class _BatchWorker(support._Worker):
    log_batch = pyqtSignal(object)


def test_running_progress_does_not_rescan_entry_results(make_window):
    window, session, _ = make_window()
    worker = support._start(window, session)

    class CountedEntries(dict):
        scans = 0

        def items(self):
            self.scans += 1
            return super().items()

    entries = CountedEntries(window.run.entries.entries)
    window.run.entries.entries = entries
    for current in range(100):
        worker.progress.emit("first", "proofread", current, 100, "正在校对")
    assert entries.scans == 0
    assert window.run.progress_view.source_rows["first"].current == 99
    assert window.pause_button.isEnabled()


def test_worker_thread_feedback_is_delivered_on_gui_thread_in_one_log_batch(make_window, monkeypatch, qapp):
    monkeypatch.setattr(task_run, "AiTaskWorker", _BatchWorker)
    window, session, _ = make_window()
    worker = support._start(window, session)
    calls, logs = [], []
    window.run.changed.connect(lambda: calls.append(threading.get_ident()))
    window.run.logged.connect(logs.append)

    def produce():
        worker.progress.emit("first", "proofread", 15, 100, "正在校对")
        worker.log.emit("first", "开始旧通道，主窗口不重复接收")
        worker.log_batch.emit((("first", "开始校对"), ("second", "已完成批次")))

    thread = threading.Thread(target=produce)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not calls and not logs
    qapp.processEvents()
    assert calls == [threading.get_ident()]
    assert logs == ["first · 开始校对\nsecond · 已完成批次"]
    assert window.logs.toPlainText().count("开始校对") == 1
    assert "旧通道" not in window.logs.toPlainText()


def test_log_batches_ignore_previous_attempt_and_completed_worker(make_window, monkeypatch):
    monkeypatch.setattr(task_run, "AiTaskWorker", _BatchWorker)
    window, session, _ = make_window()
    worker = support._start(window, session)
    logs = []
    window.run.logged.connect(logs.append)
    window.run.worker = None
    worker.log_batch.emit((("first", "开始旧任务"),))
    window.run.worker = worker
    window.run.completion_received = True
    worker.log_batch.emit((("first", "开始迟到任务"),))
    window.run.completion_received = False
    assert logs == []
    worker.log_batch.emit((("first", "[PROOFREAD_LLM_CALL_FAILED] D:\\private\\trace"), ("first", "正在校对")))
    assert logs == ["first · 正在校对"]
