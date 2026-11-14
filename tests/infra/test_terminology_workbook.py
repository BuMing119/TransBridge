from dataclasses import replace

from openpyxl import load_workbook
import pytest

from tests.infra.test_terminology_export import conflict_snapshot
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.infra.terminology_export import write_terminology_export


def test_workbook_contains_linked_summary_minority_and_all_evidence(tmp_path):
    snapshot = replace(conflict_snapshot(), kind="conflict_workbook")
    path = tmp_path / "conflicts.xlsx"
    write_terminology_export(path, snapshot)
    book = load_workbook(path)
    try:
        assert book.sheetnames == ["冲突主表", "少数译名证据", "全部证据"]
        main, minority, evidence = book.worksheets
        assert main.max_row == 2
        assert minority.max_row == evidence.max_row == 3  # tied counts retain both
        assert main["G2"].hyperlink.target == "#'少数译名证据'!A2"
        assert "少数译名代表证据" not in [cell.value for cell in main[1]]
        assert main.row_dimensions[2].height == 60
        assert main.column_dimensions["F"].width == 60
        for sheet in book:
            assert sheet.freeze_panes == "C2"
            assert sheet.auto_filter.ref
        assert minority["C2"].value == 1
        assert minority["D2"].value == "Dragon"
    finally:
        book.close()


def test_long_evidence_and_formula_text_are_not_lost_or_executed(tmp_path):
    snapshot = replace(conflict_snapshot(), kind="conflict_workbook")
    snapshot.evidence[0].original = "=" + "长" * 40000
    path = tmp_path / "long.xlsx"
    write_terminology_export(path, snapshot)
    book = load_workbook(path)
    try:
        sheet = book["全部证据"]
        assert sheet["D2"].data_type != "f"
        assert sheet["D2"].value + sheet["D3"].value == "'" + snapshot.evidence[0].original
        assert sheet["J2"].value == sheet["J3"].value
        assert sheet["K2"].value == "1/2"
        assert sheet["K3"].value == "2/2"
    finally:
        book.close()


def test_cancelled_workbook_keeps_existing_file_and_removes_staging(tmp_path):
    path = tmp_path / "kept.xlsx"
    path.write_bytes(b"existing")
    token = QueryCancellation()
    token.cancel()
    with pytest.raises(QueryCancelled):
        write_terminology_export(path, replace(conflict_snapshot(), kind="conflict_workbook"), cancellation=token)
    assert path.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [path]
