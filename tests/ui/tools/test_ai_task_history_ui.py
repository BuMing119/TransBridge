from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.ui.tools.ai_translator import task_history_dialog, task_registry, task_result_dialog
from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog
from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry, project_directory
from transbridge.ui.tools.ai_translator.task_result_dialog import TaskResultDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        QTest.qWait(5)
    raise AssertionError("Qt background operation did not complete")


def _record(run_id="past-run", state="cancelled"):
    sources = []
    for key, label, statuses in (
        ("first", "第一个来源", ("cancelled", "not_started")),
        ("second", "第二个来源", ("not_applied",)),
    ):
        candidates = tuple(
            PostProcessCandidate(
                run_id=run_id,
                entry_key=EntryKey(SourceNamespace(key), f"{key}-{index}"),
                before_revision=EntryRevision(),
                original=f"Original {key} {index}",
                before_text=f"原译文 {index}",
                text=f"候选译文 {key} {index}",
                stage=2,
                accepted=False,
                report_details=(("result_status", status), ("note", f"原因 {status}")),
            )
            for index, status in enumerate(statuses)
        )
        snapshot = ReportSnapshot(
            schema="transbridge.postprocess-report.v1",
            run_id=run_id,
            outcome=OperationOutcome.CANCELLED,
            input_count=len(candidates),
            accepted_count=0,
            candidates=candidates,
            stage_outcomes=(),
            diagnostics=(Diagnostic("TASK_CANCELLED", "任务已取消"),),
        )
        sources.append({"key": key, "label": label, "snapshot": snapshot.to_dict()})
    return {
        "run_id": run_id,
        "created_at": "2026-10-03T08:00:00+00:00",
        "state": state,
        "applied": False,
        "saved": False,
        "counts": {"successful": 1, "failed": 0, "unprocessed": 1, "cancelled": 1},
        "sources": sources,
    }


def test_project_directory_resolves_current_v2_project_without_variant_scope(tmp_path):
    context = RequestContext(owner_id="owner", project_id="old-project", variant_id="old-version")
    active_path = tmp_path / "current" / "project.json"
    service = SimpleNamespace(active_project_path=Mock(return_value=active_path))
    use_cases = SimpleNamespace(
        names=lambda: ("project_source_updates",),
        resolve=Mock(return_value=service),
    )
    ctx = SimpleNamespace(active_project_id="new-project")

    assert project_directory(ctx, SimpleNamespace(use_cases=use_cases), context) == active_path.parent
    passed_context = service.active_project_path.call_args.args[0]
    assert passed_context.project_id == "new-project"
    assert passed_context.variant_id is None
    assert passed_context.owner_id == context.owner_id
    assert context.project_id == "old-project" and context.variant_id == "old-version"


def test_background_record_writer_freezes_translation_failure_reasons(qapp, tmp_path):
    from transbridge.ai_translator.translator import TranslationResult
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
    from transbridge.ui.tools.ai_translator.task_record_writer import TaskRecordWriter
    from transbridge.ui.tools.ai_translator.task_scope import SourceTask

    entry = TranslationEntry("failed-id", "key", "Hello", "", 0, "")
    result = TranslationResult(failed_count=1, failed_entries=["failed-id: provider failed"])
    task = SourceTask("source", "Source", None, None, (entry,), ())
    outcome = SourceOutcome(task, translation=result, failed_keys=(entry.key,), cancelled=True)
    request = SimpleNamespace(run_id="cancelled-translation", config=SimpleNamespace(mixed_execution_order="serial"))
    session = SimpleNamespace(completed=False, saved=False, history_dir=tmp_path)
    writer = TaskRecordWriter(request, session)
    try:
        writer.write([outcome], state="cancelled")
        result.failed_entries.clear()
        _wait(qapp, lambda: not writer.busy)
        assert not writer.error
        record = TaskHistoryStore(tmp_path).load(request.run_id)
        assert record["counts"] == {"successful": 0, "failed": 1, "unprocessed": 0, "cancelled": 0}
        row = record["sources"][0]["snapshot"]["entries"][0]
        assert row["report_details"]["note"] == "failed-id: provider failed"
    finally:
        _wait(qapp, lambda: not writer.busy)


def test_completed_task_with_no_applied_entries_is_not_recorded_as_applied(qapp, tmp_path):
    from transbridge.ui.tools.ai_translator.task_record_writer import TaskRecordWriter

    writer = TaskRecordWriter(
        SimpleNamespace(run_id="all-rejected"),
        SimpleNamespace(completed=True, applied_keys=frozenset(), saved=False, history_dir=tmp_path),
    )
    writer.write([], state="completed")
    _wait(qapp, lambda: not writer.busy)
    record = TaskHistoryStore(tmp_path).load("all-rejected")
    assert not record["applied"]


def test_history_reload_restores_cancelled_and_interrupted_results(qapp, tmp_path, monkeypatch):
    store = TaskHistoryStore(tmp_path / "ai-task-history")
    store.save(_record())
    store.save(_record("interrupted-run", "running"))
    monkeypatch.setattr(task_history_dialog, "show_and_activate", lambda dialog: dialog.show())
    registry = AiTaskRegistry(SimpleNamespace(), directory_provider=lambda: tmp_path)
    dialog = TaskHistoryDialog(registry)
    try:
        _wait(qapp, lambda: not dialog.busy)
        rows = {
            dialog.tasks.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole): dialog.tasks.topLevelItem(i)
            for i in range(dialog.tasks.topLevelItemCount())
        }
        assert rows["past-run"].text(2) == "已取消"
        assert rows["interrupted-run"].text(2) == "已中断"
        assert "未处理 1" in rows["past-run"].text(3)
        assert rows["past-run"].text(4) == "未应用"
        dialog.tasks.setCurrentItem(rows["past-run"])
        dialog.open_selected()
        _wait(qapp, lambda: not dialog.busy and len(dialog._dialogs) == 1)
        result = dialog._dialogs[0]
        assert result.model.rowCount() == 2
        assert result.model.data(result.model.index(0, 4)) == "已取消"
        assert result.model.data(result.model.index(1, 4)) == "未处理"
        result.table.setCurrentIndex(result.model.index(1, 0))
        assert "原译文 1" in result.detail.toPlainText()
        assert "原因 not_started" in result.detail.toPlainText()
        assert "TASK_CANCELLED" in result.diagnostics.toPlainText()
    finally:
        _wait(qapp, lambda: not dialog.busy)
        for child in dialog._dialogs:
            child.close()
        dialog.close()


def test_result_source_selection_and_search_are_scoped(qapp):
    dialog = TaskResultDialog(_record(), source_key="second")
    try:
        assert dialog.sources.currentData() == "second"
        assert dialog.model.rowCount() == 1
        assert dialog.model.data(dialog.model.index(0, 4)) == "未应用"
        dialog.sources.setCurrentIndex(0)
        dialog.filter.setCurrentText("未处理")
        assert dialog.model.rowCount() == 1
        assert dialog.model.entries[0]["entry_key"]["local_key"] == "first-1"
        dialog.search.setText("候选译文 first 1")
        assert dialog.model.rowCount() == 1
        dialog.search.setText("second")
        assert dialog.model.rowCount() == 0
        dialog.search.clear()
        dialog.filter.setCurrentText("全部")
        assert dialog.model.rowCount() == 2
    finally:
        dialog.close()


def test_selected_entry_shows_its_batch_reason_without_copying_notes_to_every_row(qapp):
    record = _record()
    snapshot = record["sources"][0]["snapshot"]
    snapshot["diagnostics"].append(
        Diagnostic(
            "BATCH_FAILED",
            "本批请求失败",
            details=(("entry_keys", [snapshot["entries"][0]["entry_key"]]),),
        ).to_dict()
    )
    dialog = TaskResultDialog(record)
    try:
        dialog.table.setCurrentIndex(dialog.model.index(0, 0))
        assert "本批请求失败" in dialog.detail.toPlainText()
        dialog.table.setCurrentIndex(dialog.model.index(1, 0))
        assert "本批请求失败" not in dialog.detail.toPlainText()
        assert "本批请求失败" not in snapshot["entries"][0]["report_details"]["note"]
    finally:
        dialog.close()


def test_export_keeps_running_after_window_close_and_can_retry_failure(qapp, tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = task_result_dialog.export_snapshot
    calls = []

    def export(snapshot, path, format, **kwargs):
        calls.append(snapshot.candidates[0].entry_key.namespace.value)
        started.set()
        assert release.wait(3)
        if len(calls) == 1:
            raise OSError("disk full")
        return original(snapshot, path, format, **kwargs)

    monkeypatch.setattr(task_result_dialog, "export_snapshot", export)
    dialog = TaskResultDialog(_record(), source_key="second")
    dialog.show()
    target = tmp_path / "result.csv"
    try:
        dialog.export(target, "csv")
        _wait(qapp, started.is_set)
        assert dialog.busy
        assert all(not button.isEnabled() for button in dialog.export_buttons)
        assert dialog.close()
        assert not dialog.isVisible() and dialog.busy
        release.set()
        _wait(qapp, lambda: not dialog.busy)
        assert "导出失败" in dialog.export_status.text()
        assert all(button.isEnabled() for button in dialog.export_buttons)
        dialog.export(target, "csv")
        _wait(qapp, lambda: not dialog.busy)
        assert "已导出" in dialog.export_status.text()
        assert target.exists()
        assert calls == ["second", "second"]
    finally:
        release.set()
        _wait(qapp, lambda: not dialog.busy)
        dialog.close()


def test_registry_shutdown_tracks_hidden_export_and_preserves_previous_file(qapp, tmp_path, monkeypatch):
    from transbridge.application.translation.postprocess_report import CsvReportRenderer

    started, release = threading.Event(), threading.Event()
    original = CsvReportRenderer.render

    def render(self, snapshot, **kwargs):
        started.set()
        assert release.wait(3)
        return original(self, snapshot, **kwargs)

    monkeypatch.setattr(CsvReportRenderer, "render", render)
    registry = AiTaskRegistry(SimpleNamespace(), directory_provider=lambda: tmp_path)
    history = TaskHistoryDialog(registry)
    registry.history_windows.append(history)
    _wait(qapp, lambda: not history.busy)
    result = TaskResultDialog(_record())
    history._dialogs.append(result)
    target = tmp_path / "existing.csv"
    target.write_text("previous content", encoding="utf-8")
    try:
        result.export(target, "csv")
        _wait(qapp, started.is_set)
        result.close()
        history.close()
        assert registry.busy
        registry.shutdown()
        assert registry.shutting_down and result._cancel.is_set()
        assert registry.busy
        with pytest.raises(RuntimeError, match="尚未结束"):
            registry.dispose()
        release.set()
        _wait(qapp, lambda: not registry.busy)
        assert "已取消" in result.export_status.text()
        assert target.read_text(encoding="utf-8") == "previous content"
        assert not list(tmp_path.glob(".export-*"))
        registry.dispose()
    finally:
        release.set()
        _wait(qapp, lambda: not result.busy)
        result.close()
        history.close()


def test_registry_reopens_hidden_live_task_only_for_current_project(qapp, tmp_path, monkeypatch):
    current = [tmp_path / "first"]
    registry = AiTaskRegistry(SimpleNamespace(), directory_provider=lambda: current[0])
    opened = []

    def activate(window):
        opened.append(window)
        window.show()

    class Run(QObject):
        changed = pyqtSignal()
        state = "running"
        busy = True

    class Window(QWidget):
        def __init__(self, run_id, directory):
            super().__init__()
            self.request = SimpleNamespace(run_id=run_id)
            self.session = SimpleNamespace(project_dir=directory)
            self.run = Run()

        def is_running(self):
            return self.run.state == "running"

    monkeypatch.setattr(task_registry, "show_and_activate", activate)
    first, second = Window("first-run", current[0]), Window("second-run", tmp_path / "second")
    try:
        registry.register(first)
        registry.register(second)
        assert first.run.parent() is registry and second.run.parent() is registry
        assert registry.current_windows() == (first,)
        assert registry.foreground()
        assert opened == [first] and first.isVisible()
        first.close()
        assert not first.isVisible()
        assert registry.foreground() and first.isVisible()
        current[0] = tmp_path / "second"
        second.run.state = "pending_confirmation"
        assert registry.current_windows() == (second,)
        assert registry.foreground()
        assert opened[-1] is second
        current[0] = Path(tmp_path / "no-task")
        assert registry.current_windows() == ()
        assert not registry.foreground()
    finally:
        first.close()
        second.close()
