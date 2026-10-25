from __future__ import annotations

import threading

from PyQt6.QtCore import pyqtSignal

from tests.ui.tools import test_unified_task_progress as progress_support
from transbridge.application.translation.ai_request_budget import AiRequestBudgetSnapshot
from transbridge.ui.tools.ai_translator import task_run
from transbridge.ui.tools.ai_translator.request_state_presentation import RequestStatePresentation

make_window = progress_support.make_window
qapp = progress_support.qapp


class _ObservableWorker(progress_support._Worker):
    request_state_changed = pyqtSignal(object)


def _snapshot(*, preparing=2, waiting_preparation=1, waiting=3, in_flight=4):
    return AiRequestBudgetSnapshot(
        in_flight=in_flight,
        waiting=waiting,
        peak_in_flight=4,
        preparing=preparing,
        waiting_preparation=waiting_preparation,
        peak_preparing=2,
    )


def test_request_updates_are_independent_from_source_progress_and_pause_cancel(make_window, monkeypatch):
    monkeypatch.setattr(task_run, "AiTaskWorker", _ObservableWorker)
    window, session, _rendered = make_window()
    window.run.request_state.max_in_flight = 4
    worker = progress_support._start(window, session)
    run_changes = []
    window.run.changed.connect(lambda: run_changes.append(window.run.status))

    worker.request_state_changed.emit(_snapshot())

    assert window.request_status.text() == "准备中 2 · 等待准备 1 · 等待发送 3 · 模型调用中 4/4"
    assert run_changes == []  # Fast counters must not refresh every source/result row.
    worker.source_started.emit("first")
    worker.progress.emit("first", "translate", 1, 2, "已翻译一批")
    first_row = window.run.rows["first"]
    worker.source_started.emit("second")
    worker.progress.emit("second", "polish", 2, 2, "校对完成")
    assert window.run.rows["first"] == first_row
    second_row = window.run.rows["second"]
    worker.progress.emit("first", "translate", 2, 3, "继续翻译")
    assert window.run.rows["second"] == second_row
    assert "处理结束" not in window.run.rows["first"][0]

    worker.pause_state_changed.emit("paused")
    worker.request_state_changed.emit(_snapshot(in_flight=1))
    assert window.status.text() == "已暂停"
    assert "模型调用中 1/4" in window.request_status.text()
    window.run.cancel()
    worker.request_state_changed.emit(_snapshot(in_flight=0))
    assert window.status.text() == "正在取消"
    worker.publish(())
    assert window.request_status.text() == "准备中 0 · 等待准备 0 · 等待发送 0 · 模型调用中 0/4"
    worker.request_state_changed.emit(_snapshot())
    assert "模型调用中 0/4" in window.request_status.text()


def test_request_presentation_ignores_old_worker_and_duplicate_snapshots(qapp):
    old_worker = _ObservableWorker(None, ())
    worker = _ObservableWorker(None, ())
    presentation = RequestStatePresentation(4)
    changes = []
    presentation.changed.connect(changes.append)
    presentation.bind(old_worker)
    old_worker.request_state_changed.emit(_snapshot())
    presentation.bind(worker)
    old_worker.request_state_changed.emit(_snapshot(in_flight=1))
    presentation.finish(old_worker)
    assert presentation.snapshot is None

    snapshot = _snapshot(in_flight=2)
    worker.request_state_changed.emit(snapshot)
    count = len(changes)
    worker.request_state_changed.emit(snapshot)
    assert len(changes) == count
    assert presentation.snapshot == snapshot
    presentation.finish(worker)
    assert presentation.snapshot.in_flight == 0
    assert presentation.snapshot.peak_in_flight == 4


def test_monitor_thread_emissions_update_presentation_on_gui_thread(qapp):
    worker = _ObservableWorker(None, ())
    presentation = RequestStatePresentation(4)
    presentation.bind(worker)
    changed_on = []
    presentation.changed.connect(lambda _text: changed_on.append(threading.get_ident()))
    monitor = threading.Thread(target=lambda: worker.request_state_changed.emit(_snapshot()))
    monitor.start()
    monitor.join(timeout=1)
    assert not monitor.is_alive()
    qapp.processEvents()
    assert changed_on == [threading.get_ident()]
    assert presentation.snapshot == _snapshot()


def test_request_state_accepts_legacy_workers_without_the_signal(make_window):
    window, session, _rendered = make_window()
    worker = progress_support._start(window, session)
    assert window.request_status.text() == "请求状态：等待发送"
    worker.publish(())
    assert window.request_status.text() == "请求状态：已结束"
