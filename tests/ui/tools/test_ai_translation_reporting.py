from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QThread
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ai_translator.translator import TranslationResult
from transbridge.application.translation import build_polish_report_snapshot, build_translation_report_snapshot
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.tools.ai_translator._batch_translation_progress_window import _BatchTranslationProgressWindow
from transbridge.ui.tools.ai_translator._batch_translation_worker import (
    BatchTranslationSummary,
    PluginTranslationResult,
    _BatchTranslationWorker,
)
from transbridge.ui.tools.ai_translator._report_history_dialog import _parse_report_filename
from transbridge.ui.tools.ai_translator._report_render_worker import _ReportRenderWorker
from transbridge.ui.tools.ai_translator._translation_progress_window import _TranslationProgressWindow
from transbridge.ui.tools.ai_translator._translation_report_dialog import _TranslationReportDialog
from transbridge.ui.tools.ai_translator._translation_worker import _TranslationWorker
from transbridge.ui.tools.ai_translator.reporting import TranslationReportArtifacts, render_translation_report
from transbridge.ui.tools.ai_translator.result_presenter import PolishApplySummary, ResultPresenter


def _result_with_snapshot() -> TranslationResult:
    entry = TranslationEntry(
        id="entry-1",
        key="ENTRY:1",
        original="Source",
        translation="译文",
        stage=2,
        context="INFO:FULL",
    )
    result = TranslationResult(success_count=1)
    result.post_process_result = build_translation_report_snapshot(
        result,
        [entry],
        run_id="ui-report-run",
        cancelled=False,
    )
    return result


def test_translation_dialog_consumes_canonical_snapshot() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = _TranslationReportDialog(_result_with_snapshot().post_process_result)

    assert dialog._entry_table.rowCount() == 1
    assert dialog._entry_table.item(0, 0).text() == "Source"
    assert dialog._entry_table.item(0, 2).text() == "译文"
    assert dialog._entry_table.item(0, 3).text() == "已接受"
    assert dialog._issue_table.rowCount() == 0

    dialog.close()
    app.processEvents()


def test_polish_dialog_consumes_canonical_snapshot_details() -> None:
    entry = TranslationEntry(
        id="entry-polish",
        key="ENTRY:POLISH",
        original="Source",
        translation="旧译文",
        stage=1,
        context="INFO:FULL",
    )
    result = SimpleNamespace(
        original_translation="旧译文",
        polished_translation="新译文",
        confidence=0.9,
        changes=[{"aspect": "style", "before": "旧", "after": "新"}],
        note="更自然",
        needs_arbitration=False,
    )
    snapshot = build_polish_report_snapshot(
        {entry.id: result},
        [entry],
        accepted_entry_ids=(entry.id,),
        rejected_entry_ids=(),
        failed_entry_ids=(),
        run_id="polish-ui-run",
        polish_level="moderate",
    )
    app = QApplication.instance() or QApplication([])
    dialog = _TranslationReportDialog(snapshot)

    assert dialog.windowTitle() == "润色报告"
    assert dialog._entry_table.item(0, 2).text() == "新译文"
    assert dialog._entry_table.item(0, 3).text() == "已接受"
    assert dialog._entry_table.item(0, 4).text() == "90%"
    assert "style: 旧 → 新" in dialog._entry_table.item(0, 5).text()
    assert dialog._issue_table.rowCount() == 0

    dialog.close()
    app.processEvents()


def test_background_renderer_persists_all_formats_from_one_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    snapshot = _result_with_snapshot().post_process_result

    report = render_translation_report(snapshot, "Plugin")

    assert report.diagnostics == ()
    assert report.excel_path is not None
    assert {Path(path).suffix for path in report.paths} == {".json", ".csv", ".xlsx"}
    assert all(Path(path).is_file() for path in report.paths)
    assert _parse_report_filename(Path(report.excel_path).name)["mode"] == "AI 翻译"


def test_polish_presenter_builds_snapshot_with_explicit_decisions() -> None:
    entry = TranslationEntry(
        id="entry-rejected",
        key="ENTRY:REJECTED",
        original="Source",
        translation="旧译文",
        stage=1,
        context="INFO:FULL",
    )
    result = SimpleNamespace(
        original_translation="旧译文",
        polished_translation="候选译文",
        confidence=0.0,
        changes=[{"aspect": "style", "before": "旧", "after": "候选"}],
        note="用户选择保留原译文",
        needs_arbitration=False,
    )
    report = ResultPresenter.build_polish_report(
        {entry.id: result},
        [entry],
        PolishApplySummary(0, 1, 0, rejected_entry_ids=(entry.id,)),
        polish_level="moderate",
        esp_path="Plugin.esp",
    )

    assert report.snapshot.schema == "transbridge.polish-report.v1"
    assert report.snapshot.run_spec_summary["polish_counts"] == {"accepted": 0, "rejected": 1, "failed": 0}
    assert dict(report.snapshot.candidates[0].report_details)["result_status"] == "rejected"


def test_polish_report_worker_renders_off_qt_main_thread(monkeypatch) -> None:
    import transbridge.ui.tools.ai_translator._report_render_worker as worker_module

    app = QApplication.instance() or QApplication([])
    observed = {}
    expected = TranslationReportArtifacts(("report.json", "report.csv", "report.xlsx"), "report.xlsx", ())

    def render(snapshot, esp_stem):
        observed["thread"] = QThread.currentThread()
        observed["snapshot"] = snapshot
        observed["esp_stem"] = esp_stem
        return expected

    monkeypatch.setattr(worker_module, "render_snapshot_report", render)
    snapshot = _result_with_snapshot().post_process_result
    worker = _ReportRenderWorker(snapshot, "Plugin")
    completed = QSignalSpy(worker.completed)

    worker.start()
    assert worker.wait(5000)
    app.processEvents()

    assert observed == {"thread": observed["thread"], "snapshot": snapshot, "esp_stem": "Plugin"}
    assert observed["thread"] is not app.thread()
    assert len(completed) == 1
    assert completed[0][0] == expected
    worker.deleteLater()


def test_dialog_exposes_partial_render_diagnostics_and_successful_paths(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    snapshot = _result_with_snapshot().post_process_result
    excel_path = tmp_path / "report.xlsx"
    excel_path.touch()
    dialog = _TranslationReportDialog(snapshot, report_pending=True)

    dialog.set_report_render_result(
        TranslationReportArtifacts(
            (str(excel_path), str(tmp_path / "report.json")),
            str(excel_path),
            ("REPORT_RENDER_FAILED: CSV renderer failed",),
        )
    )

    assert dialog._report_paths == (str(excel_path), str(tmp_path / "report.json"))
    assert dialog._btn_excel.isEnabled()
    assert "已成功生成 2 个文件" in dialog._report_status.text()
    assert "REPORT_RENDER_FAILED" in dialog._report_status.text()
    assert dialog._report_status.accessibleName() == "报告生成状态"
    dialog.close()
    app.processEvents()


def test_history_parser_recognizes_canonical_polish_reports() -> None:
    parsed = _parse_report_filename("polish-report-0123456789abcdef.xlsx")
    mixed = _parse_report_filename("mixed-report-fedcba9876543210.xlsx")

    assert parsed["mode"] == "润色"
    assert mixed["mode"] == "混合"


def test_polish_presenter_does_not_swallow_snapshot_errors(monkeypatch) -> None:
    import transbridge.ui.tools.ai_translator.result_presenter as presenter_module

    monkeypatch.setattr(
        presenter_module,
        "build_polish_report_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("snapshot failed")),
    )

    with pytest.raises(RuntimeError, match="snapshot failed"):
        ResultPresenter.build_polish_report(
            {},
            [],
            PolishApplySummary(0, 0, 0),
            polish_level="moderate",
            esp_path=None,
        )


def test_translation_completion_callback_accepts_canonical_snapshot() -> None:
    class CollectionChanged:
        emitted = 0

        def emit(self, _collection) -> None:
            self.emitted += 1

    app = QApplication.instance() or QApplication([])
    worker = _TranslationWorker(None, [], None, esp_path="Plugin.esp")
    changed = CollectionChanged()
    window = _TranslationProgressWindow(
        worker,
        SimpleNamespace(collection=[], collection_changed=changed),
    )
    window._background_mode = True

    window._on_result(_result_with_snapshot())

    assert changed.emitted == 1
    assert "运行报告：1 条" in window._round_log.toPlainText()
    assert window._collection_synced is True

    window.close()
    worker.deleteLater()
    app.processEvents()


def test_batch_completion_callbacks_accept_canonical_snapshot() -> None:
    app = QApplication.instance() or QApplication([])
    worker = _BatchTranslationWorker([], LLMConfig(), overwrite=False)
    window = _BatchTranslationProgressWindow(worker, SimpleNamespace(slots={}))
    window._background_mode = True
    result = _result_with_snapshot()

    window._on_plugin_finished("Plugin", result)
    window._on_all_finished(
        BatchTranslationSummary(
            total_plugins=1,
            success_plugins=1,
            failed_plugins=0,
            total_success_entries=1,
            total_failed_entries=0,
            details=[PluginTranslationResult("Plugin", True, result)],
        )
    )

    assert "Plugin 完成" in window._round_log.toPlainText()
    assert "批量翻译完成" in window._round_log.toPlainText()

    window.close()
    worker.deleteLater()
    app.processEvents()
