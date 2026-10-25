"""Heavy read preparation leaves Qt responsive and never applies stale replies."""

import threading

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTableWidget
import pytest

from tests.ui.tools.ai_read_projection_support import wait_for
from tests.ui.tools.test_ai_task_history_ui import _record
from tests.ui.tools.test_polish_preview_dialog import _entry, _result
from transbridge.ui.tools.ai_translator import _polish_preview_dialog, task_result_projection
from transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer
from transbridge.ui.tools.ai_translator.polish_preview_model import ACCEPTED, FAILED, REJECTED
from transbridge.ui.tools.ai_translator.read_projection import LatestReadController
from transbridge.ui.tools.ai_translator.task_result_dialog import TaskResultDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _heartbeat():
    beats = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(threading.get_ident()))
    timer.start()
    wait_for(lambda: len(beats) >= 3)
    timer.stop()
    assert set(beats) == {threading.get_ident()}


def test_serial_reader_coalesces_pending_work_and_delivers_on_gui(qapp):
    entered, release = threading.Event(), threading.Event()
    calls, threads, replies = [], [], []

    def read(value):
        calls.append(value)
        threads.append(threading.get_ident())
        if value == "first":
            entered.set()
            assert release.wait(3)
        return value.upper()

    reader = LatestReadController(read)
    reader.ready.connect(lambda request, reply: replies.append((request, reply, threading.get_ident())))
    try:
        reader.submit("first")
        wait_for(entered.is_set)
        reader.submit("second")
        reader.submit("last")
        _heartbeat()
        assert calls == ["first"] and not replies
        release.set()
        wait_for(lambda: not reader.busy)
        assert calls == ["first", "last"]
        assert all(identity != threading.get_ident() for identity in threads)
        assert replies == [("last", "LAST", threading.get_ident())]
    finally:
        release.set()
        wait_for(lambda: not reader.busy)
        reader.deleteLater()


def test_log_selection_supersedes_blocked_read_without_parallel_io(qapp, tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.log").write_text(name, encoding="utf-8")
    viewer = _LLMLogViewer(str(tmp_path))
    viewer.show()
    viewer.stop_auto_refresh()
    wait_for(lambda: viewer._text_edit.toPlainText() == "c")
    entered, release = threading.Event(), threading.Event()
    original = viewer._read_visible_text
    calls, updates = [], []

    def read(path):
        assert threading.get_ident() != gui_thread
        calls.append(path)
        if path.endswith("a.log"):
            entered.set()
            assert release.wait(3)
        return original(path)

    gui_thread = threading.get_ident()
    monkeypatch.setattr(viewer, "_read_visible_text", read)
    viewer._text_edit.textChanged.connect(lambda: updates.append(viewer._text_edit.toPlainText()))
    try:
        viewer._log_selector.setCurrentIndex(0)
        wait_for(entered.is_set)
        viewer._log_selector.setCurrentIndex(1)
        viewer._log_selector.setCurrentIndex(2)
        _heartbeat()
        assert len(calls) == 1
        release.set()
        wait_for(lambda: not viewer._reader.busy)
        assert [path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for path in calls] == ["a.log", "c.log"]
        assert all(text == "c" for text in updates)
    finally:
        release.set()
        wait_for(lambda: not viewer._reader.busy)
        viewer.close()


def test_log_close_invalidates_inflight_read_and_reopen_loads_fresh(qapp, tmp_path, monkeypatch):
    (tmp_path / "workflow.log").write_text("latest", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    original = _LLMLogViewer._read_visible_text

    def read(path):
        entered.set()
        assert release.wait(3)
        return original(path)

    monkeypatch.setattr(_LLMLogViewer, "_read_visible_text", staticmethod(read))
    viewer = _LLMLogViewer(str(tmp_path))
    viewer.show()
    try:
        wait_for(entered.is_set)
        viewer.close()
        release.set()
        wait_for(lambda: not viewer._reader.busy)
        assert viewer._text_edit.toPlainText() == ""
        assert not viewer._refresh_timer.isActive()
        viewer.show()
        wait_for(lambda: viewer._text_edit.toPlainText() == "latest")
    finally:
        release.set()
        wait_for(lambda: not viewer._reader.busy)
        viewer.close()


def test_large_preview_prepares_off_gui_and_bulk_choice_does_not_build_cells(qapp, monkeypatch):
    entries = [_entry(str(index)) for index in range(20_000)]
    results = {
        entry.id: _result(entry.id, confidence=0 if index % 100 == 0 else 0.9) for index, entry in enumerate(entries)
    }
    entered, release = threading.Event(), threading.Event()
    original = _polish_preview_dialog.build_preview
    threads = []

    def build(request):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original(request)

    monkeypatch.setattr(_polish_preview_dialog, "build_preview", build)
    dialog = _polish_preview_dialog._PolishPreviewDialog(entries, results)
    resets = []
    dialog._model.modelReset.connect(lambda: resets.append(threading.get_ident()))
    dialog.show()
    try:
        wait_for(entered.is_set)
        _heartbeat()
        assert dialog._model.rowCount() == 0
        assert not dialog._apply_btn.isEnabled()
        release.set()
        wait_for(lambda: not dialog._loading)
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        assert resets == [threading.get_ident()]
        assert dialog._model.rowCount() == 20_000 and not isinstance(dialog._table, QTableWidget)
        updates = []
        dialog._model.dataChanged.connect(lambda *_: updates.append(True))
        dialog._on_accept_all()
        assert len(updates) == 1 and dialog._model.counts[ACCEPTED] == 19_800
        assert dialog._model.counts[FAILED] == 200
        dialog._model.set_status(1, REJECTED)
        assert dialog._model.counts[REJECTED] == 1
        assert dialog.get_results()["1"] is None and dialog.get_results()["2"] == "最终 2"
    finally:
        release.set()
        wait_for(lambda: not dialog._reader.busy)
        dialog.close()


def test_closed_preview_never_applies_late_projection(qapp, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = _polish_preview_dialog.build_preview

    def build(request):
        entered.set()
        assert release.wait(3)
        return original(request)

    monkeypatch.setattr(_polish_preview_dialog, "build_preview", build)
    dialog = _polish_preview_dialog._PolishPreviewDialog([_entry("one")], {"one": _result("one")})
    dialog.show()
    try:
        wait_for(entered.is_set)
        dialog.reject()
        release.set()
        wait_for(lambda: not dialog._reader.busy)
        assert dialog._model.rowCount() == 0
        assert dialog.result() == dialog.DialogCode.Rejected
    finally:
        release.set()
        wait_for(lambda: not dialog._reader.busy)
        dialog.close()


def test_destroyed_preview_does_not_own_or_receive_unfinished_reader(qapp, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = _polish_preview_dialog.build_preview

    def build(request):
        entered.set()
        assert release.wait(3)
        return original(request)

    monkeypatch.setattr(_polish_preview_dialog, "build_preview", build)
    dialog = _polish_preview_dialog._PolishPreviewDialog([_entry("one")], {"one": _result("one")})
    worker = dialog._reader._worker
    finished, replies = [], []
    worker.finished.connect(lambda: finished.append(True))
    dialog._reader.ready.connect(lambda *_: replies.append(True))
    try:
        wait_for(entered.is_set)
        dialog.deleteLater()
        wait_for(lambda: sip.isdeleted(dialog))
        _heartbeat()
        release.set()
        wait_for(lambda: bool(finished))
        assert not replies
    finally:
        release.set()
        wait_for(lambda: bool(finished))


def test_report_index_and_filter_are_background_and_only_latest_source_applies(qapp, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = task_result_projection.build_result_index
    threads, builds = [], []

    def build(source):
        threads.append(threading.get_ident())
        builds.append(source["key"])
        if len(builds) == 1:
            entered.set()
            assert release.wait(3)
        return original(source)

    monkeypatch.setattr(task_result_projection, "build_result_index", build)
    dialog = TaskResultDialog(_record())
    dialog.show()
    applied = []
    dialog.model.modelReset.connect(lambda: applied.append(tuple(row["original"] for row in dialog.model.entries)))
    try:
        wait_for(entered.is_set)
        dialog.sources.setCurrentIndex(1)
        dialog.search.setText("SECOND")
        _heartbeat()
        QTest.qWait(100)
        release.set()
        wait_for(lambda: not dialog._projecting)
        assert all(thread != threading.get_ident() for thread in threads)
        assert dialog.model.rowCount() == 1
        assert all("second" in original for rows in applied for original in rows)
        assert builds == ["first", "second"]
        dialog.search.clear()
        wait_for(lambda: not dialog._projecting)
        dialog.filter.setCurrentText("未应用")
        wait_for(lambda: not dialog._projecting)
        assert builds == ["first", "second"]
    finally:
        release.set()
        wait_for(lambda: not dialog._reader.busy)
        dialog.close()


def test_report_escape_ignores_pending_projection_and_reopen_recovers(qapp, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = task_result_projection.build_result_index

    def build(source):
        entered.set()
        assert release.wait(3)
        return original(source)

    monkeypatch.setattr(task_result_projection, "build_result_index", build)
    dialog = TaskResultDialog(_record())
    dialog.show()
    try:
        wait_for(entered.is_set)
        dialog.reject()
        release.set()
        wait_for(lambda: not dialog._reader.busy)
        assert dialog.model.rowCount() == 0
        dialog.show()
        wait_for(lambda: not dialog._projecting)
        assert dialog.model.rowCount() == 2
        dialog.shutdown()
        dialog.search.setText("first 1")
        dialog.filter.setCurrentText("未处理")
        QTest.qWait(100)
        assert not dialog._reader.busy and not dialog._search_timer.isActive()
        dialog.resume_exports()
        wait_for(lambda: not dialog._projecting)
        assert dialog.model.rowCount() == 1
    finally:
        release.set()
        wait_for(lambda: not dialog._reader.busy)
        dialog.close()


def test_read_failure_is_visible_and_explicit_refresh_recovers(qapp, tmp_path, monkeypatch):
    (tmp_path / "workflow.log").write_text("valid", encoding="utf-8")
    viewer = _LLMLogViewer(str(tmp_path))
    viewer.show()
    viewer.stop_auto_refresh()
    wait_for(lambda: not viewer._reader.busy)
    original = viewer._reader._reader

    def fail(_request):
        raise RuntimeError("reader unavailable")

    try:
        monkeypatch.setattr(viewer._reader, "_reader", fail)
        viewer._scan_and_refresh()
        wait_for(lambda: not viewer._reader.busy)
        assert "reader unavailable" in viewer._refresh_status.text()
        monkeypatch.setattr(viewer._reader, "_reader", original)
        viewer._scan_and_refresh()
        wait_for(lambda: not viewer._reader.busy)
        assert viewer._text_edit.toPlainText() == "valid"
        assert "reader unavailable" not in viewer._refresh_status.text()
    finally:
        viewer.close()
