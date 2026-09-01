from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QHeaderView, QTableWidgetItem

from transbridge.ui.paratranz.contribution_tab import ContributionTab
from transbridge.ui.paratranz.export_tab import ExportTab
from transbridge.ui.paratranz.files_tab import FilesTab
from transbridge.ui.paratranz.history_tab import HistoryTab
from transbridge.ui.paratranz.issues_tab import IssuesTab
from transbridge.ui.paratranz.members_tab import MembersTab
from transbridge.ui.paratranz.overview_tab import OverviewTab
from transbridge.ui.paratranz.strings_tab import StringsTab
from transbridge.ui.paratranz.terms_tab import TermsTab

_APP = QApplication.instance() or QApplication([])


class _Context(QObject):
    paratranz_permissions_changed = pyqtSignal()
    project_selected = pyqtSignal(object)
    config_changed = pyqtSignal(object)
    project_list_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(token="", user_id=None)
        self.current_project = None
        self.current_user = None

    @staticmethod
    def is_admin() -> bool:
        return False

    @staticmethod
    def is_member() -> bool:
        return False


def _settle(widget, *, width: int = 1000, height: int = 640) -> None:
    widget.resize(width, height)
    widget.show()
    _APP.processEvents()
    widget.layout().activate()
    _APP.processEvents()


def _table_geometry(table) -> tuple[tuple[int, int], ...]:
    header = table.horizontalHeader()
    return tuple((header.sectionPosition(column), table.columnWidth(column)) for column in range(table.columnCount()))


def test_overview_project_name_does_not_change_minimum_width() -> None:
    tab = OverviewTab(_Context())
    _settle(tab)
    tab.set_project({"id": None, "name": "short"})
    _APP.processEvents()
    short_width = tab.minimumSizeHint().width()

    project_name = "Remer-Custom Voiced Dwemer Specialist and Companion Simplified Chinese translation" * 8
    tab.set_project({"id": None, "name": project_name})
    _APP.processEvents()

    assert tab.minimumSizeHint().width() == short_width
    assert tab._lbl_name.full_text == project_name
    assert tab._lbl_name.toolTip() == project_name
    tab.close()


def test_paratranz_table_columns_do_not_follow_runtime_cell_contents() -> None:
    context = _Context()
    history = HistoryTab(context)
    tabs_and_tables = (
        (StringsTab(context), lambda tab: tab._table),
        (MembersTab(context), lambda tab: tab._table),
        (history, lambda tab: tab._hist_table),
        (history, lambda tab: tab._rev_table),
        (FilesTab(context), lambda tab: tab._table),
        (TermsTab(context), lambda tab: tab._table),
        (ContributionTab(context), lambda tab: tab._table),
    )

    seen_widgets = set()
    for widget, table_getter in tabs_and_tables:
        if id(widget) not in seen_widgets:
            _settle(widget)
            seen_widgets.add(id(widget))
        table = table_getter(widget)
        table.setRowCount(1)
        for column in range(table.columnCount()):
            table.setItem(0, column, QTableWidgetItem(f"column-{column}-short"))
        _APP.processEvents()
        before = _table_geometry(table)
        for column in range(table.columnCount()):
            table.setItem(0, column, QTableWidgetItem(f"column-{column}-" + "X" * 240))
        _APP.processEvents()

        assert all(
            table.horizontalHeader().sectionResizeMode(column) != QHeaderView.ResizeMode.ResizeToContents
            for column in range(table.columnCount())
        )
        assert _table_geometry(table) == before

    for widget, _ in tabs_and_tables:
        widget.close()


def test_files_progress_slot_keeps_table_vertical_position() -> None:
    tab = FilesTab(_Context())
    _settle(tab)
    idle_y = tab._table.mapTo(tab, tab._table.rect().topLeft()).y()

    tab._show_progress("正在上传文件…" + "X" * 240)
    tab.layout().activate()
    _APP.processEvents()

    assert tab._table.mapTo(tab, tab._table.rect().topLeft()).y() == idle_y
    assert tab._progress_lbl.full_text.endswith("X" * 240)
    tab.close()


def test_export_status_slot_keeps_action_buttons_in_place() -> None:
    tab = ExportTab(_Context())
    _settle(tab)
    idle_y = tab._trigger_btn.mapTo(tab, tab._trigger_btn.rect().topLeft()).y()

    tab._set_busy(True, "正在导出…")
    tab.layout().activate()
    _APP.processEvents()
    busy_y = tab._trigger_btn.mapTo(tab, tab._trigger_btn.rect().topLeft()).y()

    long_path = "D:/exports/" + "very-long-directory/" * 80 + "export.zip"
    tab._set_busy(False, long_path)
    tab.layout().activate()
    _APP.processEvents()

    assert busy_y == idle_y
    assert tab._trigger_btn.mapTo(tab, tab._trigger_btn.rect().topLeft()).y() == idle_y
    assert tab._status_lbl.full_text == long_path
    tab.close()


def test_issue_title_keeps_detail_origin_stable() -> None:
    tab = IssuesTab(_Context())
    _settle(tab)
    content_y = tab._content_view.geometry().y()

    title = "很长的讨论标题" * 100
    tab._set_title(title)
    tab.layout().activate()
    _APP.processEvents()

    assert tab._content_view.geometry().y() == content_y
    assert tab._title_lbl.full_text == title
    assert tab._title_lbl.toolTip() == title
    tab.close()


def test_terms_pager_and_contribution_summary_keep_allocated_positions() -> None:
    context = _Context()
    terms = TermsTab(context)
    _settle(terms)
    pager_x = (terms._prev_btn.geometry().x(), terms._next_btn.geometry().x())
    terms._page_label.setText("第 9999 页 / 共 999999999 条")
    terms.layout().activate()
    _APP.processEvents()
    assert (terms._prev_btn.geometry().x(), terms._next_btn.geometry().x()) == pager_x

    contribution = ContributionTab(context)
    _settle(contribution)
    total_x = contribution._lbl_total_scores.geometry().x()
    contribution._lbl_count.setText("条数：999999")
    contribution._lbl_total_scores.setText("总贡献值：-9999999999.99")
    contribution.layout().activate()
    _APP.processEvents()
    assert contribution._lbl_total_scores.geometry().x() == total_x

    terms.close()
    contribution.close()
