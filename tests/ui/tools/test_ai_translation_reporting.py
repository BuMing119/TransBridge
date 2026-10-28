from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ai_translator.translator import TranslationResult
from transbridge.application.translation import (
    build_polish_report_snapshot,
    build_translation_report_snapshot,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.tools.ai_translator._report_history_dialog import _parse_report_filename
from transbridge.ui.tools.ai_translator._translation_report_dialog import _TranslationReportDialog
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


def test_translation_dialog_save_button_is_retryable_then_locks_after_success(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    callbacks: list[dict[str, object]] = []
    messages: list[tuple[str, str]] = []

    def save_translation(**values) -> None:
        callbacks.append(values)

    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator._translation_report_dialog.QMessageBox.warning",
        lambda _parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator._translation_report_dialog.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    dialog = _TranslationReportDialog(
        _result_with_snapshot().post_process_result,
        save_translation=save_translation,
    )

    assert not dialog._btn_save.isHidden()
    dialog._btn_save.click()
    assert not dialog._btn_save.isEnabled()
    assert not dialog._btn_close.isEnabled()
    dialog.accept()
    assert dialog.result() == 0
    callbacks[0]["on_error"]("磁盘忙")
    assert dialog._btn_save.isEnabled()
    assert dialog._btn_close.isEnabled()
    assert dialog._btn_save.text() == "保存翻译"

    dialog._btn_save.click()
    callbacks[1]["on_success"]({"name": "AI-翻译后"})
    assert not dialog._btn_save.isEnabled()
    assert dialog._btn_save.text() == "已保存"
    assert messages == [
        ("保存翻译失败", "磁盘忙"),
        ("保存翻译", "翻译已保存，并已创建完成后的版本快照。"),
    ]
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
