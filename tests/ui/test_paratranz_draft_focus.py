"""Background ParaTranz refreshes preserve the active editor and its cursor."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.paratranz import issues_tab, mails_dialog, user_dialog


class _Context(QObject):
    project_selected = pyqtSignal(object)
    paratranz_permissions_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="fixture")
        self.current_user = {"id": 1, "nickname": "Known name", "bio": "Known bio", "avatar": "known.png"}
        self.current_project = {"id": 1}

    @staticmethod
    def is_admin():
        return True

    @staticmethod
    def is_member():
        return True


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, _callback, **_kwargs):
        super().__init__()

    def start(self):
        pass


@pytest.fixture
def views(layout_app, monkeypatch):
    for module in (user_dialog, mails_dialog, issues_tab):
        monkeypatch.setattr(module, "ApiWorker", _Worker)
    context = _Context()
    made = []

    def make(kind):
        classes = {
            "user": user_dialog.UserInfoDialog,
            "mails": mails_dialog.MailsDialog,
            "issues": issues_tab.IssuesTab,
        }
        widget = classes[kind](context)
        made.append(widget)
        widget.show()
        widget.activateWindow()
        settle_layout(layout_app)
        return widget

    yield context, make
    for widget in made:
        widget.close()
        widget.deleteLater()
    settle_layout(layout_app)


@pytest.mark.parametrize("field", ["nickname", "bio", "avatar"])
@pytest.mark.parametrize("selection", [False, True])
def test_identical_profile_response_preserves_insertion_and_selection(views, layout_app, field, selection):
    context, make = views
    view = make("user")
    editor = getattr(view, f"_{field}_input")
    baseline = editor.text()
    editor.setFocus()
    editor.setCursorPosition(1)
    if selection:
        editor.setSelection(1, 3)
    before = editor.cursorPosition(), editor.selectionStart(), editor.selectedText()

    view._workers[-1].result.emit((dict(context.current_user), []))
    settle_layout(layout_app)

    assert layout_app.focusWidget() is editor
    assert (editor.cursorPosition(), editor.selectionStart(), editor.selectedText()) == before
    QTest.keyClicks(editor, "XY")
    assert editor.text() == baseline[:1] + "XY" + baseline[4 if selection else 1 :]


@pytest.mark.parametrize("refresh", ["contacts", "messages"])
def test_mail_background_refresh_preserves_draft_selection(views, layout_app, refresh):
    _context, make = views
    view = make("mails")
    contacts = [{"from": 10, "to": 1, "fromUser": {"nickname": "Alice"}, "content": "Hello"}]
    view._workers[-1].result.emit(contacts)
    view._conv_list.setCurrentRow(0)
    conversation = view._workers[-1]
    if refresh == "contacts":
        view._load_mails()
    pending = view._workers[-1] if refresh == "contacts" else conversation
    editor = view._send_input
    _select_draft(editor)

    pending.result.emit(contacts if refresh == "contacts" else [{"from": 10, "content": "Reply"}])
    settle_layout(layout_app)

    _assert_draft_selection(layout_app, editor)


def test_issue_detail_response_preserves_draft_selection(views, layout_app):
    context, make = views
    view = make("issues")
    context.project_selected.emit(context.current_project)
    view._workers[-1].result.emit([{"id": 10, "title": "Discussion"}])
    view._list.setCurrentRow(0)
    pending = view._workers[-1]
    _select_draft(view._reply_input)

    pending.result.emit({"activities": [{"content": "Reply"}]})
    settle_layout(layout_app)

    _assert_draft_selection(layout_app, view._reply_input)


def _select_draft(editor):
    editor.setPlainText("Pending draft")
    editor.setFocus()
    cursor = editor.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def _assert_draft_selection(app, editor):
    assert app.focusWidget() is editor
    assert editor.toPlainText() == "Pending draft"
    assert editor.textCursor().anchor() == 1
    assert editor.textCursor().position() == 4
    assert editor.textCursor().selectedText() == "end"
