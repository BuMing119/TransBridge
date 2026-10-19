"""Project/file selection and dialog actions use consistent visible state."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QGroupBox, QPushButton
import pytest

from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.qt_palette import compile_palette
from transbridge.ui.foundation.theme_service import ThemeSnapshot
from transbridge.ui.foundation.visual_style import compile_application_stylesheet
from transbridge.ui.paratranz import files_tab, project_panel
from transbridge.ui.paratranz.overview_tab import EditProjectDialog
from transbridge.ui.paratranz.terms_tab import TermFormDialog


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    font, palette, stylesheet = QFont(app.font()), QPalette(app.palette()), app.styleSheet()
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    if font_id >= 0:
        app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet("")
    yield app
    app.setStyleSheet(stylesheet)
    app.setPalette(palette)
    app.setFont(font)
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


class _Context(QObject):
    project_selected = pyqtSignal(object)
    config_changed = pyqtSignal(object)
    project_list_changed = pyqtSignal()
    paratranz_permissions_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="", user_id=7, base_url="https://fixture.invalid")
        self.current_project = None
        self.current_user = {"id": 7}
        self.active_project_id = "fixture-local-project"
        self.project_revision = 1
        self.mine_project_ids = set()

    @staticmethod
    def is_admin():
        return True


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, _fn, **_kwargs):
        super().__init__()

    def start(self):
        pass


@pytest.fixture
def panel(qapp, monkeypatch):
    monkeypatch.setattr(project_panel, "ApiWorker", _Worker)
    context = _Context()
    widget = project_panel.ProjectListPanel(context)
    widget._on_projects_loaded([{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}])
    widget.resize(240, 640)
    widget.show()
    qapp.processEvents()
    yield context, widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def _click_project(widget, index):
    item = widget._list.item(index)
    QTest.mouseClick(widget._list.viewport(), Qt.MouseButton.LeftButton, pos=widget._list.visualItemRect(item).center())


def test_keyboard_project_selection_matches_mouse_without_duplicate_browse(panel):
    context, widget = panel
    chosen = []
    widget.project_chosen.connect(chosen.append)
    _click_project(widget, 0)
    QTest.keyClick(widget._list, Qt.Key.Key_Down)
    assert widget._list.currentItem().text() == "Beta"
    assert context.current_project["name"] == "Beta"
    assert [project["id"] for project in chosen] == [1, 2]
    assert not widget._workers


def test_project_search_keeps_browsing_context_and_reports_visible_count(panel):
    context, widget = panel
    chosen = []
    widget.project_chosen.connect(chosen.append)
    _click_project(widget, 1)
    widget._search.setText("beta")
    assert widget._status.text() == "匹配 1 / 2 个项目"
    assert widget._list.currentItem().text() == "Beta"
    widget._search.setText("no-match")
    assert widget._list.count() == 0
    assert widget._status.text() == "没有匹配的项目（共 2 个）"
    assert not widget._bind_btn.isEnabled()
    assert context.current_project["id"] == 2
    widget._search.clear()
    assert widget._status.text() == "共 2 个项目"
    assert widget._list.currentItem().text() == "Beta"
    assert widget._bind_btn.isEnabled()
    assert len(chosen) == 1


def test_project_search_does_not_replace_loading_failure_or_connection_feedback(panel):
    context, widget = panel
    _click_project(widget, 0)
    context.config.token = "fixture-only"
    widget.load_projects()
    pending = widget._workers[-1]
    widget._search.setText("a")
    assert widget._list.count() == 0
    assert widget._status.text() == "加载中…"
    assert not widget._bind_btn.isEnabled()
    pending.error.emit("Fixture failure")
    widget._search.clear()
    assert widget._status.text() == "加载失败：Fixture failure"
    widget.load_projects()
    stale = widget._workers[-1]
    context.config.token = ""
    context.config_changed.emit(None)
    stale.result.emit([{"id": 9, "name": "Expired result"}])
    stale.error.emit("Expired failure")
    assert widget._status.text() == "未配置 Token"
    assert widget._list.count() == 0
    assert not widget._bind_btn.isEnabled()


def test_project_search_cannot_enable_binding_during_membership_validation(panel):
    context, widget = panel
    context.config.token = "fixture-only"
    _click_project(widget, 0)
    widget._bind_selected_project()
    worker = widget._workers[-1]
    widget._search.setText("alpha")
    assert not widget._bind_btn.isEnabled()
    assert widget._status.text() == "正在验证当前账号的项目成员身份…"
    worker.finished.emit()
    assert widget._status.text() == "匹配 1 / 2 个项目"
    assert widget._bind_btn.isEnabled()


@pytest.fixture
def files(qapp, monkeypatch):
    monkeypatch.setattr(files_tab, "ApiWorker", _Worker)
    widget = files_tab.FilesTab(_Context())
    widget._on_files_loaded([{"id": 81, "name": "a.json", "hash": "fixture-hash", "words": 42}])
    widget.resize(900, 640)
    widget.show()
    qapp.processEvents()
    widget._table.selectRow(0)
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("action", ("clear", "empty", "project"))
def test_file_details_clear_with_their_selection(files, action):
    assert files._det_id.text() == "81"
    if action == "clear":
        files._table.clearSelection()
    elif action == "empty":
        files._on_files_loaded([])
    else:
        files._on_project_changed(None)
    assert files._selected_file() is None
    assert not files._download_btn.isEnabled()
    assert all(
        label.text() == "—"
        for label in (files._det_id, files._det_hash, files._det_words, files._det_created, files._det_updated)
    )


def test_file_details_are_visible_and_explain_missing_selection(files):
    assert files._det_id.isVisible()
    group = files._det_id.parentWidget()
    assert isinstance(group, QGroupBox)
    files._table.clearSelection()
    assert "选择文件查看详情" in group.title()
    files._table.selectRow(0)
    assert group.title() == "文件详情"
    assert files._det_id.text() == "81"


def test_file_selection_refresh_updates_details_and_cleared_project_ignores_stale_result(files):
    files._on_files_loaded([{"id": 82, "name": "b.json", "hash": "new-hash", "words": 16}])
    if files._selected_file() is not None:
        assert files._det_id.text() == "82"
    files._on_project_changed({"id": 7})
    pending = files._workers[-1]
    files._on_project_changed(None)
    pending.result.emit([{"id": 90, "name": "Old project file"}])
    assert files._table.rowCount() == 0
    assert files._det_id.text() == "—"


def _dialog(kind):
    if kind == "new-project":
        widget = project_panel.NewProjectDialog()
        widget._name.setText("Fixture project")
        return widget, "创建项目", widget._name
    if kind == "edit-project":
        widget = EditProjectDialog({"name": "Fixture project"})
        return widget, "保存修改", widget._name
    term = None if kind == "new-term" else {"term": "dragon", "translation": "巨龙"}
    widget = TermFormDialog(term)
    widget._term_input.setText("dragon")
    widget._trans_input.setText("巨龙")
    return widget, "添加术语" if term is None else "保存修改", widget._term_input


_FORM_KINDS = ("new-project", "edit-project", "new-term", "edit-term")


@pytest.mark.parametrize("kind", _FORM_KINDS)
@pytest.mark.parametrize("key", (Qt.Key.Key_Return, Qt.Key.Key_Escape))
def test_form_primary_role_keeps_existing_enter_escape_and_payload(qapp, kind, key):
    widget, action, input_widget = _dialog(kind)
    buttons = {button.text(): button for button in widget.findChildren(QPushButton)}
    try:
        assert buttons[action].property("tbComponentKind") == "button"
        assert buttons[action].property("tbSemanticState") == "primary"
        assert buttons["取消"].property("tbComponentKind") == "button"
        assert buttons["取消"].property("tbSemanticState") in (None, "default")
        assert buttons[action].autoDefault()
        assert not buttons[action].isDefault()
        original_data = widget.get_data()
        widget.show()
        input_widget.setFocus()
        qapp.processEvents()
        QTest.keyClick(input_widget, key)
        assert widget.result() == (
            QDialog.DialogCode.Accepted if key == Qt.Key.Key_Return else QDialog.DialogCode.Rejected
        )
        assert widget.get_data() == original_data
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


def _apply_style(widget, font_size, scheme):
    font = QFont(widget.font())
    font.setPointSizeF(font_size)
    widget.setFont(font)
    if scheme is None:
        return
    definition = create_builtin_registry().resolve(DEFAULT_THEME_ID, scheme)
    primitives = definition.tokens.primitives
    tokens = replace(
        definition.tokens,
        primitives=replace(primitives, typography=replace(primitives.typography, body_size=font_size)),
    )
    snapshot = ThemeSnapshot(
        1,
        definition.manifest.provider_id,
        definition.manifest.theme_id,
        scheme,
        definition.fingerprint,
        tokens,
        compile_palette(definition),
        "paratranz-interaction-test",
    )
    app = QApplication.instance()
    app.setPalette(snapshot.palette)
    app.setStyleSheet(compile_application_stylesheet(snapshot))


@pytest.mark.parametrize("kind", _FORM_KINDS)
@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK))
def test_form_actions_fit_narrow_dialogs_with_theme_and_large_font(qapp, kind, font_size, scheme):
    widget, _action, _input = _dialog(kind)
    _apply_style(widget, font_size, scheme)
    widget.resize(440, 650)
    widget.show()
    for _ in range(5):
        qapp.processEvents()
    try:
        assert widget.width() == 440
        for button in widget.findChildren(QPushButton):
            assert button.font().pointSizeF() == font_size
            assert button.width() >= button.minimumSizeHint().width()
            assert button.height() >= button.minimumSizeHint().height()
            assert widget.rect().contains(button.geometry())
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK))
def test_search_empty_feedback_fits_the_narrow_project_sidebar(qapp, panel, font_size, scheme):
    _context, widget = panel
    _apply_style(widget, font_size, scheme)
    widget.resize(200, 640)
    widget._search.setText("no-match")
    for _ in range(5):
        qapp.processEvents()
    assert widget.width() == 200
    assert widget._status.height() >= widget._status.heightForWidth(widget._status.width())
    assert widget.rect().contains(widget._status.geometry())
    assert widget._bind_btn.width() >= widget._bind_btn.minimumSizeHint().width()
    assert not widget._bind_btn.isEnabled()


@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize("scheme", (None, ThemeScheme.LIGHT, ThemeScheme.DARK))
def test_file_detail_empty_hint_stays_visible_with_large_fonts(qapp, files, font_size, scheme):
    _apply_style(files, font_size, scheme)
    files._table.clearSelection()
    for _ in range(5):
        qapp.processEvents()
    assert files.width() == 900
    assert files._detail_box.isVisible()
    assert files._detail_box.width() >= files._detail_box.minimumSizeHint().width()
    assert files.rect().contains(files._detail_box.mapTo(files, files._detail_box.rect().bottomRight()))
    for label in (files._det_id, files._det_hash, files._det_words, files._det_created, files._det_updated):
        assert label.font().pointSizeF() == font_size
        assert label.height() >= label.minimumSizeHint().height()
        assert files.rect().contains(label.mapTo(files, label.rect().bottomRight()))
