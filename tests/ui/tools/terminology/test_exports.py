import csv
from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.infra.test_terminology_export import conflict_snapshot, decision
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.ai_translator.term_formats import load_terms_json
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.effective import EffectiveSnapshotStatus
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow


@pytest.fixture
def export_window():
    main_thread = get_ident()
    reads = []

    def describe(query, **_kwargs):
        reads.append((query, get_ident()))
        return SimpleNamespace(
            effective_snapshot=SimpleNamespace(
                status=EffectiveSnapshotStatus.READY,
                decisions=tuple(decision(i) for i in range(305)) + (replace(decision(999), suppressed=True),),
            )
        )

    services = TerminologyUiServices(
        browsing=SimpleNamespace(
            describe=describe,
            list_effective_terms=lambda *args, **kwargs: None,
            list_browsable_draft_terms=lambda *args, **kwargs: None,
        )
    )
    window = TerminologyWindow(
        TerminologyPresenter(services, RequestContext("operator", project_id="project", variant_id="variant"))
    )
    window._preflight_started = True
    try:
        yield window, reads, main_thread
    finally:
        window.close()
        window._exports._runner._pool.waitForDone(3000)
        QApplication.processEvents()


def test_whole_effective_export_ignores_visible_page_filters_and_runs_in_background(
    export_window, tmp_path, monkeypatch
):
    window, reads, main_thread = export_window
    path = tmp_path / "terms.json"
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.export_controller.QFileDialog.getSaveFileName",
        lambda *args: (str(path), "JSON (*.json)"),
    )
    window.effective_view.filters.search.setText("only one term")
    window._exports.main_button.click()
    _wait(lambda: not window._exports._busy)
    assert len(load_terms_json(path)) == 305
    assert reads[0][0].filters.search == ""
    assert reads[0][1] != main_thread
    assert "305" in window._exports.status.text()


def test_cancel_save_dialog_writes_nothing_and_restores_export_action(export_window, tmp_path, monkeypatch):
    window, _reads, _thread = export_window
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.export_controller.QFileDialog.getSaveFileName", lambda *args: ("", "")
    )
    window._exports.terms_button.click()
    _wait(lambda: not window._exports._busy)
    assert list(tmp_path.iterdir()) == []
    assert window._exports.main_button.isEnabled()
    assert window._exports.status.text() == "已取消导出。"


def test_export_failure_is_visible_without_success_message(export_window, tmp_path, monkeypatch):
    window, _reads, _thread = export_window
    path = tmp_path / "unsupported.xml"
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.export_controller.QFileDialog.getSaveFileName",
        lambda *args: (str(path), "JSON (*.json)"),
    )
    window._exports.terms_button.click()
    _wait(lambda: not window._exports._busy)
    assert "导出失败" in window._exports.status.text()
    assert not path.exists()


@pytest.mark.parametrize("summary", [False, True])
def test_conflict_export_reads_all_groups_and_preserves_published_resolutions(
    export_window, tmp_path, monkeypatch, summary
):
    window, _reads, _thread = export_window
    base = conflict_snapshot()
    resolved = tuple(
        SimpleNamespace(**{**vars(base.items[0]), "conflict_group_id": f"group-{index}"}) for index in range(101)
    )
    unresolved = tuple(SimpleNamespace(**{**vars(item), "status": "unresolved"}) for item in resolved)
    build = SimpleNamespace(ref="build", conflicts=unresolved, evidence=base.evidence)
    version = SimpleNamespace(build_ref="build", conflicts=resolved, decisions=base.decisions)
    commands = SimpleNamespace(
        latest_build_result=lambda *_args: build,
        active_draft=lambda *_args: None,
        latest_version=lambda *_args: version,
    )
    window.presenter.services = replace(window.presenter.services, commands=commands)
    path = tmp_path / "conflicts.csv"
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.export_controller.QFileDialog.getSaveFileName",
        lambda *args: (str(path), "CSV (*.csv)"),
    )
    window.conflicts_view.search.setText("filter does not match")
    (window._exports.summary_button if summary else window._exports.conflict_button).click()
    _wait(lambda: not window._exports._busy)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == (101 if summary else 202)
    assert {row["处理状态"] for row in rows} == {"已统一"}
    assert "101" in window._exports.conflict_status.text()


def test_closing_window_during_export_preserves_existing_target(export_window, tmp_path, monkeypatch):
    from transbridge.infra import terminology_export

    window, _reads, _thread = export_window
    path = tmp_path / "terms.json"
    path.write_text("existing", encoding="utf-8")
    started, release = Event(), Event()
    dump = terminology_export.dump_terms_json

    def staged_write(staged, entries):
        dump(staged, entries)
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(terminology_export, "dump_terms_json", staged_write)
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.export_controller.QFileDialog.getSaveFileName",
        lambda *args: (str(path), "JSON (*.json)"),
    )
    try:
        window._exports.main_button.click()
        _wait(started.is_set)
        window.close()
    finally:
        release.set()
        window._exports._runner._pool.waitForDone(3000)
    assert path.read_text(encoding="utf-8") == "existing"
    assert list(tmp_path.iterdir()) == [path]


def test_workbook_button_exports_one_file_with_tabs_and_adds_extension(export_window, tmp_path, monkeypatch):
    from openpyxl import load_workbook

    window, _reads, _thread = export_window
    snapshot = conflict_snapshot()
    build = SimpleNamespace(ref="build", conflicts=snapshot.items, evidence=snapshot.evidence)
    window.presenter.services = replace(
        window.presenter.services,
        commands=SimpleNamespace(
            latest_build_result=lambda *_args: build,
            active_draft=lambda *_args: None,
        ),
    )
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.export_controller.QFileDialog.getSaveFileName",
        lambda *args: (str(tmp_path / "conflicts"), "Excel 工作簿 (*.xlsx)"),
    )
    window._exports.workbook_button.click()
    _wait(lambda: not window._exports._busy)
    book = load_workbook(tmp_path / "conflicts.xlsx")
    try:
        assert book.sheetnames == ["冲突主表", "少数译名证据", "全部证据"]
    finally:
        book.close()
