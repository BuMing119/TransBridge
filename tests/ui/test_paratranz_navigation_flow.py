"""Exercise grouped cloud navigation and history paging without network access."""

from __future__ import annotations

from dataclasses import replace
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.foundation.theme_service import ThemeSnapshot
from transbridge.ui.foundation.visual_style import compile_application_stylesheet
from transbridge.ui.paratranz import history_tab
from transbridge.ui.paratranz.navigation import ParaTranzNavigation
from transbridge.ui.paratranz.widget import ParaTranzWidget


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    font, stylesheet = QFont(app.font()), app.styleSheet()
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    if font_id >= 0:
        app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet("")
    yield app
    app.setStyleSheet(stylesheet)
    app.setFont(font)
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


class _Context(QObject):
    paratranz_permissions_changed = pyqtSignal()
    project_selected = pyqtSignal(object)
    config_changed = pyqtSignal(object)
    project_list_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="", user_id=None)
        self.current_project = None
        self.current_user = None
        self.mine_project_ids = set()
        self.active_project_id = None

    def select(self, project):
        self.current_project = project
        self.project_selected.emit(project)

    @staticmethod
    def is_admin():
        return False

    @staticmethod
    def is_member():
        return False


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def start(self):
        pass

    def complete(self):
        self.result.emit(self.fn())


@pytest.fixture
def root(qapp, monkeypatch):
    for module in (
        "project_panel",
        "overview_tab",
        "files_tab",
        "strings_tab",
        "terms_tab",
        "members_tab",
        "history_tab",
        "contribution_tab",
        "export_tab",
        "issues_tab",
    ):
        monkeypatch.setattr(importlib.import_module(f"transbridge.ui.paratranz.{module}"), "ApiWorker", _Worker)
    context = _Context()
    widget = ParaTranzWidget(context)
    widget.resize(1100, 720)
    widget.show()
    qapp.processEvents()
    yield context, widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def _click_tab(bar, index):
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center())


def test_group_navigation_preserves_page_contract_and_remembers_each_group(qapp, root):
    context, widget = root
    context.config.token = "fixture-only"
    context.select({"id": 7, "name": "Fixture"})
    original_pages = (
        widget._overview,
        widget._files,
        widget._strings,
        widget._terms,
        widget._members,
        widget._history,
        widget._contribution,
        widget._export,
        widget._issues,
    )
    assert tuple(widget._tabs.widget(i) for i in range(widget._tabs.count())) == original_pages
    navigation = widget._navigation
    _click_tab(navigation.groups, 1)
    _click_tab(navigation.pages, 1)
    assert widget._tabs.currentWidget() is widget._strings
    _click_tab(navigation.groups, 2)
    _click_tab(navigation.pages, 1)
    assert widget._tabs.currentWidget() is widget._members
    _click_tab(navigation.groups, 1)
    assert widget._tabs.currentWidget() is widget._strings
    _click_tab(navigation.groups, 2)
    assert widget._tabs.currentWidget() is widget._members
    navigation.groups.setFocus()
    QTest.keyClick(navigation.groups, Qt.Key.Key_Left)
    assert widget._tabs.currentWidget() is widget._strings
    navigation.pages.setFocus()
    QTest.keyClick(navigation.pages, Qt.Key.Key_Right)
    assert widget._tabs.currentWidget() is widget._terms

    for index, page in enumerate(original_pages):
        widget._tabs.setCurrentIndex(index)
        assert widget._tabs.currentWidget() is page
        assert navigation.pages.tabData(navigation.pages.currentIndex()) == index
    qapp.processEvents()


def test_project_and_token_empty_states_hide_stale_pages_and_offer_next_step(qapp, root):
    context, widget = root
    assert widget._content.currentWidget() is widget._empty
    assert "通用设置 → ParaTranz" in widget._empty_message.text()
    assert not widget._choose_project.isVisible()
    assert not widget._navigation.groups.isEnabled()

    context.config.token = "fixture-only"
    context.config_changed.emit(context.config)
    qapp.processEvents()
    assert widget._choose_project.isVisible()
    QTest.mouseClick(widget._choose_project, Qt.MouseButton.LeftButton)
    assert widget._project_panel._list.hasFocus()
    assert "同步目标" in widget._empty_message.text()

    context.select({"id": 7, "name": "A long cloud project " * 20})
    widget._tabs.setCurrentIndex(2)
    qapp.processEvents()
    assert widget._strings.isVisible()
    assert widget._navigation.project_label.full_text.endswith("A long cloud project " * 20)
    context.select(None)
    qapp.processEvents()
    assert widget._content.currentWidget() is widget._empty
    assert not widget._strings.isVisible()
    assert widget._navigation.project_label.full_text == "尚未选择项目"
    assert not widget._navigation.groups.isEnabled()

    context.select({"id": 8, "name": "Second project"})
    assert widget._content.currentWidget() is widget._tabs
    context.config.token = ""
    context.config_changed.emit(context.config)
    qapp.processEvents()
    assert widget._content.currentWidget() is widget._empty
    assert not widget._strings.isVisible()


def _style(widget, font_size, scheme):
    font = QFont(widget.font())
    font.setPointSizeF(font_size)
    widget.setFont(font)
    if scheme is None:
        return
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    primitives = definition.tokens.primitives
    typography = replace(primitives.typography, body_size=font_size)
    tokens = replace(definition.tokens, primitives=replace(primitives, typography=typography))
    snapshot = ThemeSnapshot(
        1,
        definition.manifest.provider_id,
        definition.manifest.theme_id,
        scheme,
        definition.fingerprint,
        tokens,
        compile_palette(definition),
        "paratranz-navigation-test",
    )
    widget.setPalette(snapshot.palette)
    widget.setStyleSheet(compile_application_stylesheet(snapshot))


@pytest.mark.parametrize("width", (360, 480))
@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK))
def test_group_navigation_fits_narrow_content_and_long_project_names(qapp, width, font_size, scheme):
    widget = ParaTranzNavigation()
    _style(widget, font_size, scheme)
    widget.set_project({"id": 7, "name": "大型模组汉化项目" * 40})
    widget.resize(width, 160)
    widget.show()
    qapp.processEvents()
    try:
        for group in range(4):
            _click_tab(widget.groups, group)
            qapp.processEvents()
            assert widget.width() == width
            assert widget.minimumSizeHint().width() <= width
            for bar in (widget.groups, widget.pages):
                assert bar.font().pointSizeF() == font_size
                assert widget.rect().contains(bar.geometry())
                for index in range(bar.count()):
                    assert bar.rect().contains(bar.tabRect(index))
        assert "大型模组汉化项目" * 40 in widget.project_label.toolTip()
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


@pytest.fixture
def history(qapp, monkeypatch):
    monkeypatch.setattr(history_tab, "ApiWorker", _Worker)
    calls = []

    class API:
        def __init__(self, **kwargs):
            pass

        def get_project_history(self, project_id, **kwargs):
            calls.append(("history", project_id, kwargs))
            return [{} for _ in range(50 if kwargs["page"] == 1 else 0)]

        def list_file_revisions(self, project_id, **kwargs):
            calls.append(("revisions", project_id, kwargs))
            return [{} for _ in range(50 if kwargs["page"] == 1 else 1)]

    monkeypatch.setattr(history_tab, "ParatranzHistoryAPI", API)
    context = _Context()
    widget = history_tab.HistoryTab(context)
    widget.resize(720, 640)
    widget.show()
    context.select({"id": 7, "name": "Fixture"})
    widget._workers[0].complete()
    widget._workers[1].complete()
    qapp.processEvents()
    yield context, widget, calls
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_history_next_empty_page_can_return_and_query_keeps_current_page(history):
    _context, widget, calls = history
    pager = widget._hist_pager
    assert pager.next.isEnabled()
    assert not pager.previous.isEnabled()
    QTest.mouseClick(pager.next, Qt.MouseButton.LeftButton)
    assert len(widget._workers) == 3
    assert not pager.next.isEnabled()
    widget._workers[-1].complete()
    assert calls[-1][2]["page"] == 2
    assert "没有更多记录" in pager.status.text()
    assert pager.previous.isEnabled()
    assert not pager.next.isEnabled()
    QTest.mouseClick(widget._hist_refresh_btn, Qt.MouseButton.LeftButton)
    widget._workers[-1].complete()
    assert calls[-1][2]["page"] == 2
    QTest.mouseClick(pager.previous, Qt.MouseButton.LeftButton)
    widget._workers[-1].complete()
    assert calls[-1][2]["page"] == 1
    assert widget._hist_table.rowCount() == 50


def test_page_keyboard_and_focus_changes_make_only_one_request(qapp, history):
    _context, widget, calls = history
    spin = widget._page_spin
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, "3")
    assert len(widget._workers) == 2
    QTest.keyClick(spin, Qt.Key.Key_Return)
    qapp.processEvents()
    assert len(widget._workers) == 3
    widget._workers[-1].complete()
    assert calls[-1][2]["page"] == 3
    QTest.mouseClick(widget._hist_pager.previous, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert len(widget._workers) == 4
    widget._workers[-1].complete()
    assert calls[-1][2]["page"] == 2


def test_filter_changes_and_project_clear_discard_pending_results(history):
    context, widget, calls = history
    QTest.mouseClick(widget._hist_pager.next, Qt.MouseButton.LeftButton)
    stale = widget._workers[-1]
    widget._uid_input.setText("12")
    stale.complete()
    assert widget._hist_table.rowCount() == 0
    assert widget._page_spin.value() == 1
    assert "点击查询" in widget._hist_pager.status.text()
    QTest.mouseClick(widget._hist_refresh_btn, Qt.MouseButton.LeftButton)
    widget._workers[-1].complete()
    assert calls[-1][2]["uid"] == 12
    assert calls[-1][2]["page"] == 1
    widget.load_history()
    pending = widget._workers[-1]
    widget.load_revisions()
    pending_revision = widget._workers[-1]
    context.select(None)
    pending.complete()
    pending_revision.complete()
    assert widget._hist_table.rowCount() == widget._rev_table.rowCount() == 0
    assert widget._hist_pager.status.text() == widget._rev_pager.status.text() == "请先选择项目"


@pytest.mark.parametrize("history_type", ("", "text"))
def test_specific_text_history_does_not_offer_ineffective_paging(history, history_type):
    _context, widget, calls = history
    widget._type_combo.setCurrentIndex(widget._type_combo.findData(history_type))
    widget._tid_input.setText("42")
    QTest.mouseClick(widget._hist_refresh_btn, Qt.MouseButton.LeftButton)
    widget._workers[-1].complete()
    assert calls[-1][2]["tid"] == 42
    assert "此查询不分页" in widget._hist_pager.status.text()
    assert not widget._hist_pager.page.isEnabled()
    assert not widget._hist_pager.next.isEnabled()


def test_file_revision_pages_and_errors_remain_visible_and_recoverable(qapp, history):
    _context, widget, calls = history
    widget._tabs.setCurrentIndex(1)
    qapp.processEvents()
    pager = widget._rev_pager
    QTest.mouseClick(pager.next, Qt.MouseButton.LeftButton)
    widget._workers[-1].error.emit("Fixture connection failed")
    assert widget._rev_table.rowCount() == 0
    assert "Fixture connection failed" in pager.status.text()
    assert "刷新重试" in pager.status.text()
    QTest.mouseClick(widget._rev_refresh_btn, Qt.MouseButton.LeftButton)
    widget._workers[-1].complete()
    assert calls[-1][0] == "revisions"
    assert calls[-1][2]["page"] == 2
    assert calls[-1][2]["page_size"] == 50
    assert pager.previous.isEnabled()
    assert not pager.next.isEnabled()
    assert "本页 1 条" in pager.status.text()


@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK))
def test_narrow_root_keeps_group_controls_and_empty_state_inside_content(qapp, root, font_size, scheme):
    context, widget = root
    _style(widget, font_size, scheme)
    widget.resize(900, 720)
    qapp.processEvents()
    try:
        for project in (None, {"id": 7, "name": "Long project " * 40}, None):
            context.config.token = "fixture-only"
            context.select(project)
            qapp.processEvents()
            assert widget.width() == 900
            navigation = widget._navigation
            assert navigation.width() >= navigation.minimumSizeHint().width()
            assert widget.rect().contains(navigation.mapTo(widget, navigation.rect().bottomRight()))
            assert widget.rect().contains(widget._content.mapTo(widget, widget._content.rect().bottomRight()))
    finally:
        widget.setStyleSheet("")


@pytest.mark.parametrize("width", (360, 480))
@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK))
def test_history_filters_and_pager_fit_narrow_content(qapp, history, width, font_size, scheme):
    _context, widget, _calls = history
    _style(widget, font_size, scheme)
    widget.resize(width, 640)
    qapp.processEvents()
    assert widget.width() == width
    assert widget.minimumSizeHint().width() <= width
    for control in (
        widget._type_combo,
        widget._uid_input,
        widget._tid_input,
        widget._hist_refresh_btn,
        widget._hist_pager.previous,
        widget._hist_pager.page,
        widget._hist_pager.next,
    ):
        assert control.font().pointSizeF() == font_size
        assert control.width() >= control.minimumSizeHint().width()
        assert widget.rect().contains(control.mapTo(widget, control.rect().topLeft()))
        assert widget.rect().contains(control.mapTo(widget, control.rect().bottomRight()))
    widget._hist_pager.show_error(2, "无法连接 ParaTranz，请检查网络连接。")
    for _ in range(5):
        qapp.processEvents()
    status = widget._hist_pager.status
    assert status.height() >= status.heightForWidth(status.width())
    assert widget.rect().contains(status.mapTo(widget, status.rect().bottomRight()))
