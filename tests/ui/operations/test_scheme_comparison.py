from dataclasses import replace
import time

from openpyxl import load_workbook
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QPushButton
import pytest

from tests.ui.operations.test_strict_write_routes import _context, feature as feature_fixture, qapp as qapp_fixture
from tests.ui.operations.test_write_schemes import _schemes
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.application.terminology_profiles.comparison import compare_scheme_translations
from transbridge.infra.terminology_export import write_terminology_export
from transbridge.ui.operations.scheme_comparison_controller import install_scheme_comparison
from transbridge.ui.operations.scheme_comparison_dialog import SchemeComparisonDialog
from transbridge.ui.operations.write_dialog import WritePlanDialog
from transbridge.ui.operations.write_schemes import PREFIX, comparison_sources

feature = feature_fixture
qapp = qapp_fixture


def _report(tmp_path, feature, count=2):
    context, source = _context(tmp_path, count), _schemes()
    draft = feature.feature.create_draft(context, count > 1, {})
    sources = comparison_sources(draft.request)
    projections = source.resolve(tuple(key for key, _ in source.choices))
    return compare_scheme_translations(sources, projections), sources, projections


def test_comparison_keeps_source_identity_and_only_differing_translations(tmp_path, feature):
    report, sources, projections = _report(tmp_path, feature)
    assert report.total_count == 4 and len(report.items) == 2
    assert len({row.source for row in report.items}) == 2
    assert all(row.translations == ("甲译名", "乙译名") for row in report.items)
    assert all(row.original == "First" and row.current == "译文0" for row in report.items)
    unchanged = compare_scheme_translations(sources, (projections[0], projections[0]))
    assert not unchanged.items
    triple = compare_scheme_translations(sources, (*projections, projections[0]))
    assert triple.items[0].translations == ("甲译名", "乙译名", "甲译名")
    token = QueryCancellation()
    token.cancel()
    with pytest.raises(QueryCancelled):
        compare_scheme_translations(sources, projections, cancellation=token)
    with pytest.raises(ValueError, match="两个"):
        compare_scheme_translations(sources, projections[:1])


def test_workbook_has_linkable_rows_long_text_and_formula_protection(tmp_path, feature):
    report, _, _ = _report(tmp_path, feature, 1)
    long_text = "完整译文" * 10000
    row = replace(report.items[0], original="=1+1", translations=(long_text, "乙"))
    report = replace(report, items=(row,))
    path = tmp_path / "difference.xlsx"
    assert write_terminology_export(path, report) == 1
    book = load_workbook(path)
    try:
        assert book.sheetnames == ["译文差异", "方案信息"]
        sheet = book["译文差异"]
        assert sheet["C2"].value == "'=1+1" and sheet["C2"].data_type != "f"
        assert sheet.max_row == 3
        assert sheet["A2"].value == sheet["A3"].value == row.source
        assert sheet["B2"].value == sheet["B3"].value == row.entry_key
        assert sheet["E2"].value + sheet["E3"].value == long_text
        assert sheet.freeze_panes == "C2" and sheet.auto_filter.ref
        assert "方案 1 版本" in [cell.value for cell in book["方案信息"]["A"]]
    finally:
        book.close()
    before = path.read_bytes()
    token = QueryCancellation()
    token.cancel()
    with pytest.raises(QueryCancelled):
        write_terminology_export(path, report, cancellation=token)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".difference.xlsx.*"))


def _wait(predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def test_compare_from_export_without_output_directory_then_save_workbook(tmp_path, feature, qapp, monkeypatch):
    context, source = _context(tmp_path), _schemes()
    initial = feature.feature.create_draft(context, False, {"export_schemes": source})
    host = WritePlanDialog(feature.feature.mapper.present("compare", 1, initial), context)
    install_scheme_comparison(host, lambda fields: feature.feature.edit_draft(initial, fields))
    host.show()
    button = next(button for button in host.findChildren(QPushButton) if button.text() == "查看译文差异…")
    assert not button.isEnabled()
    for key, _ in source.choices:
        host._field_edits[PREFIX + key].setChecked(True)
    assert button.isEnabled()
    assert host._field_edits["scheme_directory"].text() == ""
    button.click()
    dialog = host.findChild(SchemeComparisonDialog)
    assert dialog is not None
    _wait(lambda: dialog.report is not None)
    assert dialog.table.model().rowCount() == 1
    assert "甲译名" in dialog.details.toPlainText() and "乙译名" in dialog.details.toPlainText()
    output = tmp_path / "compare.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    dialog.export_button.click()
    _wait(lambda: output.exists() and dialog.export_button.isEnabled())
    assert "已导出 1 条" in dialog.status.text()
    assert source.service.selection("project", "variant") is None
    dialog.close()
    host.close()
    qapp.processEvents()


def test_empty_comparison_can_still_export_version_information(tmp_path, feature):
    report, sources, projections = _report(tmp_path, feature, 1)
    report = compare_scheme_translations(sources, (projections[0], projections[0]))
    path = tmp_path / "empty.xlsx"
    write_terminology_export(path, report)
    book = load_workbook(path)
    assert book["译文差异"].max_row == 1
    assert book["方案信息"].max_row > 5
    book.close()


def test_close_cancels_background_comparison_without_late_ui_update(tmp_path, feature, qapp, monkeypatch):
    from threading import Event, get_ident

    started, release, finished = Event(), Event(), Event()
    tokens = []
    gui_thread = get_ident()

    def slow_compare(_sources, _projections, *, cancellation):
        assert get_ident() != gui_thread
        tokens.append(cancellation)
        started.set()
        release.wait(3)
        try:
            cancellation.raise_if_cancelled()
        finally:
            finished.set()

    monkeypatch.setattr(
        "transbridge.ui.operations.scheme_comparison_controller.compare_scheme_translations", slow_compare
    )
    context, source = _context(tmp_path), _schemes()
    initial = feature.feature.create_draft(context, False, {"export_schemes": source})
    host = WritePlanDialog(feature.feature.mapper.present("cancel", 1, initial), context)
    install_scheme_comparison(host, lambda fields: feature.feature.edit_draft(initial, fields))
    for key, _ in source.choices:
        host._field_edits[PREFIX + key].setChecked(True)
    host.scheme_comparison_requested.emit(host.edited_values())
    _wait(started.is_set)
    host.findChild(SchemeComparisonDialog).close()
    assert tokens[0].is_cancelled
    release.set()
    _wait(finished.is_set)
    host.close()
    qapp.processEvents()
