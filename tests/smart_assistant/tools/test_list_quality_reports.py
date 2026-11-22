"""Parity between the registered assistant tool and the desktop Excel history dialog."""

from datetime import datetime
import os
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.tools.ai_translator._report_history_dialog import _ReportHistoryDialog


@pytest.fixture
def list_reports(tmp_path, monkeypatch):
    monkeypatch.setattr("transbridge.ai_translator.report_history.get_data_dir", lambda: str(tmp_path))
    register_all()
    ctx = ExecutionContext(app_context=SimpleNamespace(runtime_context=RequestContext("owner", session_id="session")))
    return lambda **args: ToolRegistry.get("list_quality_reports").execute(args, ctx)


def test_desktop_and_assistant_show_same_exported_files(list_reports, tmp_path):
    # The filename timestamps on older workbooks must take precedence over their later copy time.
    old = "Source_translate_report_20240102_123456.xlsx"
    mixed = "mixed-report-fedcba9876543210.xlsx"
    directory = tmp_path / "ai_translator"
    for plugin, filename, day in [("First", old, 10), ("Second", mixed, 8), ("First", "other.xlsx", 4)]:
        reports = directory / plugin / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        file = reports / filename
        file.write_bytes(filename.encode())
        mtime = datetime(2024, 1, day, 12).timestamp()
        os.utime(file, (mtime, mtime))
    (reports / "report.json").touch()
    (reports / "folder.xlsx").mkdir()
    retired = tmp_path / "task-store" / "artifacts" / "old-task" / "reports"
    retired.mkdir(parents=True)
    (retired / "old-task.xlsx").touch()

    app = QApplication.instance() or QApplication([])
    dialog = _ReportHistoryDialog()
    try:
        table = dialog._table
        desktop_rows = [tuple(table.item(i, column).text() for column in range(5)) for i in range(table.rowCount())]
        result = list_reports()
        assert result.success, result.message
        files = result.data["files"]
        assert [row["name"] for row in files] == [mixed, "other.xlsx", old]
        assert [(row["mode"], row["esp_stem"]) for row in files] == [
            ("混合", "Second"),
            ("?", "First"),
            ("翻译", "Source"),
        ]
        assert [
            (row["name"], row["mode"], row["esp_stem"], row["timestamp"].replace("T", " "), f"{row['size']} B")
            for row in files
        ] == desktop_rows
        assert files[-1]["timestamp"] == "2024-01-02T12:34:56"
        assert all(set(row) == {"name", "mode", "esp_stem", "timestamp", "size"} for row in files)
        assert list_reports(limit=1).data["files"] == files[:1]
    finally:
        dialog.close()
        app.processEvents()


def test_missing_catalog_is_empty_without_a_project_or_task_store(list_reports, tmp_path):
    assert list_reports().data == {"files": []}
    assert not (tmp_path / "ai_translator").exists()


def test_default_limit_returns_newest_50_across_plugins(list_reports, tmp_path):
    for index in range(51):
        directory = tmp_path / "ai_translator" / f"Plugin{index % 2}" / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        report = directory / f"review-{index}.xlsx"
        report.touch()
        os.utime(report, (1_700_000_000 + index, 1_700_000_000 + index))
    assert [row["name"] for row in list_reports().data["files"]] == [f"review-{i}.xlsx" for i in range(50, 0, -1)]


@pytest.mark.parametrize("limit", [0, -1, True, "5"])
def test_invalid_limit_is_rejected(list_reports, limit):
    assert not list_reports(limit=limit).success
