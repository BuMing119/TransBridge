"""Large display projections stay bounded while original/export data stays complete."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.ai_read_projection_support import wait_for
from tests.ui.tools.test_ai_task_history_ui import _record
from tests.ui.tools.test_polish_preview_dialog import _entry, _result
from transbridge.ui.tools.ai_translator import task_result_dialog
from transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer
from transbridge.ui.tools.ai_translator.bounded_read_text import (
    MAX_DISPLAY_CHARS,
    MAX_DISPLAY_LINE_CHARS,
    MAX_DISPLAY_LINES,
    bounded_display_text,
)
from transbridge.ui.tools.ai_translator.polish_preview_model import ACCEPTED, PolishPreviewModel, build_preview
from transbridge.ui.tools.ai_translator.task_result_dialog import TaskResultDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _assert_bounded(text):
    assert "显示内容已截断" in text
    assert len(text) <= MAX_DISPLAY_CHARS
    assert len(text.split("\n")) <= MAX_DISPLAY_LINES + 2
    assert max(map(len, text.split("\n"))) <= MAX_DISPLAY_LINE_CHARS


@pytest.mark.parametrize("text", ["", "短文本\n", "first\n\nlast", "中文 <b>原文</b>\r\n"])
@pytest.mark.parametrize("tail", [True, False])
def test_normal_text_is_unchanged(text, tail):
    assert bounded_display_text(text, tail=tail, full_text_hint="完整内容另存") == text


@pytest.mark.parametrize("tail", [True, False])
def test_long_line_and_document_are_bounded_with_explicit_notice(tail):
    text = "FIRST" + "x" * (2 * 1024 * 1024) + "LAST"
    displayed = bounded_display_text(text, tail=tail, full_text_hint="完整内容另存")
    _assert_bounded(displayed)
    assert "完整内容另存" in displayed
    assert ("LAST" if tail else "FIRST") in displayed


def test_tail_preserves_recent_lines_and_caps_line_count():
    text = "\n".join(f"line {index}" for index in range(3000))
    displayed = bounded_display_text(text, tail=True, full_text_hint="完整内容另存")
    _assert_bounded(displayed)
    assert displayed.endswith("line 2999") and "line 0\n" not in displayed


def test_log_delivers_bounded_tail_without_changing_original_file(qapp, tmp_path):
    payload = "x" * (1024 * 1024) + "\n" + "recent line\n" * 20_000 + "LATEST"
    path = tmp_path / "workflow.log"
    path.write_bytes(payload.encode("utf-8"))
    viewer = _LLMLogViewer(str(tmp_path))
    viewer.show()
    viewer.stop_auto_refresh()
    try:
        wait_for(lambda: not viewer._reader.busy)
        displayed = viewer._text_edit.toPlainText()
        _assert_bounded(displayed)
        assert displayed.endswith("LATEST")
        assert "原始日志文件" in displayed
        assert path.read_bytes().decode("utf-8") == payload
    finally:
        viewer.close()


def test_preview_bounds_cell_and_tooltip_but_keeps_complete_accepted_candidate(qapp):
    payload = "完整候选" + "x" * (2 * 1024 * 1024)
    entry, result = _entry("long"), _result("long")
    result.polished_translation = payload
    model = PolishPreviewModel()
    model.set_projection(build_preview(([entry], {entry.id: result})))
    index = model.index(0, 4)
    assert model.data(index) == payload[:240]
    tooltip = model.data(index, Qt.ItemDataRole.ToolTipRole)
    assert len(tooltip) < 2200 and "显示已截断" in tooltip
    assert model.rows[0].values[4] == payload
    model.set_status(0, ACCEPTED)
    assert model.results() == {entry.id: payload}


def test_report_bounds_diagnostics_details_and_tooltips_but_exports_complete_data(qapp, tmp_path, monkeypatch):
    record = _record()
    snapshot = record["sources"][0]["snapshot"]
    payload = "x" * (2 * 1024 * 1024) + "END"
    entry = snapshot["entries"][0]
    entry["original"] = payload
    entry["report_details"]["note"] = payload
    snapshot["diagnostics"][0]["message"] = payload
    snapshot["diagnostics"][0]["details"] = {"entry_key": entry["entry_key"]}
    exports = []

    def export(restored, path, _format, *, cancel_event):
        exports.append(restored)
        return path

    monkeypatch.setattr(task_result_dialog, "export_snapshot", export)
    dialog = TaskResultDialog(record)
    dialog.show()
    try:
        wait_for(lambda: not dialog._projecting)
        _assert_bounded(dialog.diagnostics.toPlainText())
        dialog.table.setCurrentIndex(dialog.model.index(0, 0))
        _assert_bounded(dialog.detail.toPlainText())
        tooltip = dialog.model.data(dialog.model.index(0, 1), Qt.ItemDataRole.ToolTipRole)
        assert len(tooltip) < 2200 and "显示已截断" in tooltip
        dialog.export(tmp_path / "synthetic.csv", "csv")
        wait_for(lambda: not dialog.busy)
        assert exports[0].diagnostics[0].message == payload
        assert exports[0].candidates[0].original == payload
        assert dict(exports[0].candidates[0].report_details)["note"] == payload
        assert snapshot["diagnostics"][0]["message"] == payload
        assert entry["original"] == payload
    finally:
        dialog.close()
