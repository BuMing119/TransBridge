"""Background reads cannot replace profile drafts or cleared score context."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLineEdit, QMessageBox
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.paratranz import contribution_tab, user_dialog


class _Context(QObject):
    project_selected = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="fixture")
        self.current_user = {"id": 1, "nickname": "Known name", "bio": "Known bio", "avatar": "known.png"}


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, callback, **_kwargs):
        super().__init__()
        self.callback = callback

    def start(self):
        pass


@pytest.fixture
def views(layout_app, monkeypatch):
    monkeypatch.setattr(user_dialog, "ApiWorker", _Worker)
    monkeypatch.setattr(contribution_tab, "ApiWorker", _Worker)
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: notices.append(args[1:]))
    context = _Context()
    made = []

    def make(kind):
        widget = {"user": user_dialog.UserInfoDialog, "contribution": contribution_tab.ContributionTab}[kind](context)
        made.append(widget)
        widget.show()
        settle_layout(layout_app)
        return widget

    yield context, make, notices
    for widget in made:
        widget.close()
        widget.deleteLater()
    settle_layout(layout_app)


@pytest.mark.parametrize("field", ["nickname", "bio", "avatar"])
@pytest.mark.parametrize("draft", ["My new draft", ""])
def test_profile_load_preserves_each_edited_field_and_updates_untouched_data(views, field, draft):
    _context, make, _notices = views
    view = make("user")
    editor = getattr(view, f"_{field}_input")
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    QTest.keyClicks(editor, draft)
    loaded = {
        "id": 1,
        "username": "account",
        "credit": 9,
        "nickname": "Loaded",
        "bio": "Loaded bio",
        "avatar": "new.png",
    }

    view._workers[-1].result.emit((loaded, [{"projectId": 2, "stringId": 3}]))

    assert editor.text() == draft
    for other in {"nickname", "bio", "avatar"} - {field}:
        assert getattr(view, f"_{other}_input").text() == loaded[other]
    assert view._lbl_username.text() == "account"
    assert view._lbl_credit.text() == "9"
    assert view._activity_table.rowCount() == 1


def test_profile_save_after_background_load_keeps_original_api_field_contract(views, monkeypatch):
    _context, make, _notices = views
    view = make("user")
    view._nickname_input.selectAll()
    QTest.keyClicks(view._nickname_input, "  My draft  ")
    view._workers[-1].result.emit(({"id": 1, "nickname": "Loaded", "bio": "Server bio", "avatar": "server.png"}, []))
    calls = []
    monkeypatch.setattr(
        user_dialog, "ParatranzUserAPI", lambda **kwargs: SimpleNamespace(update_user=lambda *args: calls.append(args))
    )

    view._save_profile()
    view._workers[-1].callback()

    assert calls == [(1, {"nickname": "My draft", "bio": "Server bio", "avatar": "server.png"})]


@pytest.mark.parametrize("field", ["nickname", "bio", "avatar"])
def test_profile_load_does_not_interrupt_input_method_preedit(views, layout_app, field):
    _context, make, _notices = views
    view = make("user")
    editor = getattr(view, f"_{field}_input")
    editor.setFocus()
    baseline = editor.text()
    editor.setCursorPosition(len(baseline))
    layout_app.sendEvent(editor, QInputMethodEvent("composing", []))

    view._workers[-1].result.emit(({"id": 1, field: "Loaded"}, []))
    commit = QInputMethodEvent()
    commit.setCommitString("新输入")
    layout_app.sendEvent(editor, commit)

    assert editor.text() == baseline + "新输入"


@pytest.mark.parametrize("select_row", [False, True])
def test_contribution_clear_resets_summary_and_rejects_late_results(views, select_row):
    context, make, notices = views
    view = make("contribution")
    context.project_selected.emit({"id": 1})
    view._workers[-1].result.emit([{"value": 5}])
    if select_row:
        view._table.selectRow(0)
    view.load_scores()
    pending = view._workers[-1]

    context.project_selected.emit(None)

    assert view._table.rowCount() == 0
    assert view._scores == []
    assert view._lbl_count.text() == "条数：0"
    assert view._lbl_total_scores.text() == "总贡献值：0.00"
    pending.result.emit([{"value": 9}])
    pending.error.emit("old project failure")
    assert view._table.rowCount() == 0
    assert view._lbl_total_scores.text() == "总贡献值：0.00"
    assert not notices


def test_contribution_current_project_still_loads_and_reports_errors(views):
    context, make, notices = views
    view = make("contribution")
    context.project_selected.emit({"id": 1})
    old = view._workers[-1]
    context.project_selected.emit({"id": 2})
    current = view._workers[-1]
    old.result.emit([{"value": 99}])
    old.error.emit("old failure")
    assert not notices
    current.error.emit("current failure")
    assert notices[-1][-1] == "current failure"
    view.load_scores()
    view._workers[-1].result.emit([{"value": 2}, {"value": 3}])
    assert view._lbl_count.text() == "条数：2"
    assert view._lbl_total_scores.text() == "总贡献值：5.00"


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize("point_size", [9, 14])
def test_profile_edits_remain_readable_in_a_narrow_themed_window(views, layout_app, mode, point_size):
    _context, make, _notices = views
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    view = make("user")
    apply_layout_font(view, service.snapshot(), point_size)
    view.resize(540, 620)
    settle_layout(layout_app)
    try:
        assert view.width() == 540
        for editor in view.findChildren(QLineEdit):
            assert editor.font().pointSizeF() == point_size
            assert editor.height() >= editor.fontMetrics().height()
            assert view.rect().contains(editor.mapTo(view, editor.rect().center()))
    finally:
        service.close()
