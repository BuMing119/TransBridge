from __future__ import annotations

from functools import partial
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.test_unified_ai_execution import _request, _task
from transbridge.ui.tools.ai_translator import source_execution
from transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker
from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def viewer(qapp, tmp_path):
    window = _LLMLogViewer(str(tmp_path))
    window.show()
    window._refresh_timer.setInterval(10)
    qapp.processEvents()
    yield window
    window.close()


def test_log_directory_is_announced_before_source_work_completes(monkeypatch, tmp_path):
    monkeypatch.setattr(source_execution, "WorkflowLogStore", partial(WorkflowLogStore, log_base=tmp_path))
    events = []

    def translate(_executor, task, store):
        assert events == [("ready", task.key, store.log_dir)]
        assert Path(store.log_dir).is_dir()
        store.write_line("translation_call_001", "live model response")
        return SimpleNamespace(failed_count=0)

    monkeypatch.setattr(SourceExecutor, "_translate", translate)
    worker = AiTaskWorker(_request(), (_task(),))
    worker.log_ready.connect(lambda key, directory: events.append(("ready", key, directory)))
    worker.completed.connect(lambda outcomes: events.append(("done", outcomes)))
    worker.run()
    assert events[0][0] == "ready" and events[1][0] == "done"
    assert events[1][1][0].log_dir == events[0][2]


def test_timer_discovers_new_files_and_updates_current_selection(viewer, tmp_path):
    assert viewer._log_selector.count() == 0
    current = tmp_path / "workflow.log"
    current.write_text("first", encoding="utf-8")
    QTest.qWait(40)
    assert viewer._log_selector.count() == 1
    assert viewer._text_edit.toPlainText() == "first"
    current.write_text("first\nnew response", encoding="utf-8")
    (tmp_path / "proofread_call_001.log").write_text("other response", encoding="utf-8")
    QTest.qWait(40)
    assert viewer._log_selector.count() == 2
    assert viewer._log_selector.currentData() == str(current)
    assert viewer._text_edit.toPlainText() == "first\nnew response"


def test_unavailable_log_directory_is_announced_before_work(monkeypatch):
    store = SimpleNamespace(log_dir="", close=lambda: None)
    monkeypatch.setattr(source_execution, "WorkflowLogStore", lambda *_args, **_kwargs: store)
    events = []

    def translate(_executor, task, _store):
        assert events == [(task.key, "")]
        return SimpleNamespace(failed_count=0)

    monkeypatch.setattr(SourceExecutor, "_translate", translate)
    worker = AiTaskWorker(_request(), (_task(),))
    worker.log_ready.connect(lambda key, path: events.append((key, path)))
    worker.run()
    assert events == [("first", "")]


def test_scrolled_reader_keeps_content_until_returning_to_tail(viewer, tmp_path, qapp):
    current = tmp_path / "workflow.log"
    old = "".join(f"line {i}\n" for i in range(300))
    current.write_text(old, encoding="utf-8")
    viewer._scan_and_refresh()
    qapp.processEvents()
    scrollbar = viewer._text_edit.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(4)
    current.write_text(old + "new response", encoding="utf-8")
    QTest.qWait(40)
    assert scrollbar.value() == 4
    assert viewer._text_edit.toPlainText() == old
    assert "有新日志" in viewer._refresh_status.text()
    scrollbar.setValue(scrollbar.maximum())
    QTest.qWait(40)
    assert viewer._text_edit.toPlainText().endswith("new response")
    assert scrollbar.value() == scrollbar.maximum()


def test_unchanged_files_are_not_reread_and_close_stops_timer(viewer, tmp_path, monkeypatch):
    (tmp_path / "workflow.log").write_text("response", encoding="utf-8")
    viewer._scan_and_refresh()

    def unexpected_read(_path):
        pytest.fail("unchanged log was reread")

    monkeypatch.setattr(viewer, "_read_visible_text", unexpected_read)
    QTest.qWait(40)
    viewer.close()
    assert not viewer._refresh_timer.isActive()
    (tmp_path / "second.log").write_text("later", encoding="utf-8")
    QTest.qWait(40)
    assert viewer._log_selector.count() == 1


def test_large_logs_use_bounded_tail_read(tmp_path, monkeypatch):
    monkeypatch.setattr(_LLMLogViewer, "_MAX_VISIBLE_BYTES", 1024)
    current = tmp_path / "large.log"
    current.write_text("earlier\n" * 2000 + "latest response", encoding="utf-8")
    content = _LLMLogViewer._read_visible_text(str(current))
    assert content.startswith("[日志过大")
    assert content.endswith("latest response")
    assert len(content) < 1200


def test_manual_refresh_updates_scrolled_text_and_reopening_restarts_timer(viewer, tmp_path, qapp):
    current = tmp_path / "workflow.log"
    old = "".join(f"line {i}\n" for i in range(300))
    current.write_text(old, encoding="utf-8")
    viewer._scan_and_refresh()
    qapp.processEvents()
    scrollbar = viewer._text_edit.verticalScrollBar()
    scrollbar.setValue(4)
    current.write_text(old + "manual update", encoding="utf-8")
    viewer._refresh_btn.click()
    assert viewer._text_edit.toPlainText().endswith("manual update")
    assert scrollbar.value() == 4
    viewer.close()
    assert not viewer._refresh_timer.isActive()
    viewer.show()
    assert viewer._refresh_timer.isActive()


def test_read_error_is_visible_and_recovers(viewer, tmp_path, monkeypatch):
    (tmp_path / "workflow.log").write_text("response", encoding="utf-8")
    original = viewer._read_visible_text

    def fail(_path):
        raise PermissionError("access denied")

    monkeypatch.setattr(viewer, "_read_visible_text", fail)
    viewer._scan_and_refresh()
    assert "无法读取日志" in viewer._refresh_status.text()
    monkeypatch.setattr(viewer, "_read_visible_text", original)
    QTest.qWait(40)
    assert viewer._text_edit.toPlainText() == "response"
    assert "无法读取日志" not in viewer._refresh_status.text()
