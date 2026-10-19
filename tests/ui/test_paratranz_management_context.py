"""Management requests must not follow the user into a different project."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QFileDialog, QLineEdit, QMessageBox, QPushButton
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.paratranz import files_tab, members_tab, overview_tab, terms_tab


class _Context(QObject):
    project_selected = pyqtSignal(object)
    paratranz_permissions_changed = pyqtSignal()
    project_list_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="synthetic")
        self._project = None

    @property
    def current_project(self):
        return self._project

    @current_project.setter
    def current_project(self, value):
        self._project = value
        self.project_selected.emit(value)

    def is_admin(self):
        return True

    def update_paratranz_members(self, _pid, _members):
        pass


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, call):
        super().__init__()
        self.call = call

    def start(self):
        pass


_DATA = {
    "files": [{"id": 11, "name": "synthetic.json", "total": 3}],
    "members": [{"id": 11, "permission": 1, "user": {"id": 10, "nickname": "Synthetic member"}}],
    "terms": {"data": [{"id": 11, "term": "Dragon", "translation": "Synthetic translation"}], "rowCount": 1},
    "overview": [{"total": 3, "translated": 1}],
}
_PAGES = {
    "files": (files_tab, files_tab.FilesTab),
    "members": (members_tab, members_tab.MembersTab),
    "terms": (terms_tab, terms_tab.TermsTab),
    "overview": (overview_tab, overview_tab.OverviewTab),
}


@pytest.fixture
def management(layout_app, monkeypatch):
    notices = []
    for method in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, method, lambda *args: notices.append(args))
    made = []

    def make(kind):
        module, cls = _PAGES[kind]
        monkeypatch.setattr(module, "ApiWorker", _Worker)
        context = _Context()
        page = cls(context)
        context.current_project = {"id": 7, "name": "Original project"}
        page._workers[-1].result.emit(_DATA[kind])
        if hasattr(page, "_table"):
            page._table.selectRow(0)
        page.resize(760, 560)
        page.show()
        settle_layout(layout_app)
        made.append(page)
        return context, page, notices

    yield make
    for page in made:
        page.close()
        page.deleteLater()
    settle_layout(layout_app)


def _accept_modal(context=None):
    dialog = QApplication.activeModalWidget()
    if context is not None:
        context.current_project = {"id": 8, "name": "New project"}
    if isinstance(dialog, QMessageBox):
        dialog.button(QMessageBox.StandardButton.Yes).click()
    else:
        for editor in dialog.findChildren(QLineEdit):
            if not editor.text():
                editor.setText("42")
        button = next(button for button in dialog.findChildren(QPushButton) if button.text() != "取消")
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)


_MODAL_ACTIONS = (
    ("files", "_delete_file"),
    ("members", "_add_member"),
    ("members", "_remove_member"),
    ("terms", "_add_term"),
    ("terms", "_edit_term"),
    ("terms", "_delete_term"),
    ("overview", "_edit_project"),
    ("overview", "_delete_project"),
)


@pytest.mark.parametrize("kind,action", _MODAL_ACTIONS)
def test_modal_action_is_cancelled_if_the_project_changes(management, kind, action):
    context, page, _notices = management(kind)
    before = len(page._workers)
    QTimer.singleShot(0, lambda: _accept_modal(context))
    getattr(page, action)()
    assert len(page._workers) == before + 1, "only the new project's read may be queued"
    assert context.current_project["id"] == 8


@pytest.mark.parametrize("action", ("_upload_new", "_reupload", "_import_translation", "_download_translation"))
def test_file_chooser_cannot_retarget_the_selected_file_to_another_project(management, monkeypatch, action):
    context, page, _notices = management("files")

    def choose(*_args):
        context.current_project = {"id": 8, "name": "New project"}
        return "synthetic.json", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    before = len(page._workers)
    getattr(page, action)()
    assert len(page._workers) == before + 1


@pytest.mark.parametrize("kind,action", _MODAL_ACTIONS)
def test_late_management_completion_preserves_the_current_project(management, kind, action):
    context, page, notices = management(kind)
    QTimer.singleShot(0, _accept_modal)
    getattr(page, action)()
    operation = page._workers[-1]
    context.current_project = {"id": 8, "name": "New project"}
    before = len(page._workers)
    operation.result.emit({"id": 7, "name": "Old response"})
    operation.error.emit("late failure")
    assert context.current_project["id"] == 8
    assert len(page._workers) == before
    assert not notices


@pytest.mark.parametrize("kind", ("files", "members", "terms", "overview"))
def test_cleared_project_ignores_old_reads_and_errors(management, kind):
    context, page, notices = management(kind)
    old = page._workers[0]
    context.current_project = None
    old.result.emit(_DATA[kind])
    old.error.emit("old project failure")
    assert not notices
    if kind == "overview":
        assert page._prog_bar.format() == "请先选择项目"
        assert page._stat_labels["total"].text() == "—"
    else:
        assert page._table.rowCount() == 0


def test_file_project_switch_releases_the_new_pages_action_gate(management, monkeypatch):
    context, page, notices = management("files")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("synthetic.json", ""))
    page._upload_new()
    old_operation = page._workers[-1]
    assert page._operation_busy
    context.current_project = {"id": 8, "name": "New project"}
    assert not page._operation_busy
    assert page._upload_btn.isEnabled()
    page._upload_new()
    current_operation = page._workers[-1]
    old_operation.error.emit("old upload failed")
    assert page._operation_busy
    assert not notices
    current_operation.error.emit("current upload failed")
    assert not page._operation_busy
    assert "current upload failed" in notices[-1][-1]


def test_invalid_member_id_keeps_the_permission_draft_open(management, monkeypatch):
    _context, page, _notices = management("members")
    observed = []

    def warn(*_args):
        dialog = QApplication.activeModalWidget()
        observed.append(dialog is not None and dialog.isVisible())
        if dialog is not None:
            QTimer.singleShot(0, dialog.reject)

    monkeypatch.setattr(QMessageBox, "warning", warn)

    def enter_invalid():
        dialog = QApplication.activeModalWidget()
        editor = dialog.findChild(QLineEdit)
        editor.setText("not-a-user-id")
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_Return)

    QTimer.singleShot(0, enter_invalid)
    page._add_member()
    assert observed == [True]


def test_member_id_can_be_corrected_with_enter_without_reentering_permissions(management, monkeypatch):
    _context, page, _notices = management("members")
    observed, calls, selections = [], [], []

    def warn(*_args):
        dialog = QApplication.activeModalWidget()
        editor = dialog.findChild(QLineEdit)
        combo = dialog.findChild(QComboBox)
        observed.append((editor.text(), combo.currentData()))

        def correct():
            selections.append(editor.hasSelectedText())
            editor.selectAll()
            QTest.keyClicks(editor, "42")
            QTest.keyClick(editor, Qt.Key.Key_Return)

        QTimer.singleShot(0, correct)

    monkeypatch.setattr(QMessageBox, "warning", warn)
    monkeypatch.setattr(
        members_tab,
        "ParatranzMembersAPI",
        lambda **_kwargs: SimpleNamespace(add_member=lambda *args: calls.append(args)),
    )

    def enter_invalid():
        dialog = QApplication.activeModalWidget()
        editor = dialog.findChild(QLineEdit)
        dialog.findChild(QComboBox).setCurrentIndex(1)
        editor.setText("invalid")
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_Return)

    QTimer.singleShot(0, enter_invalid)
    page._add_member()
    page._workers[-1].call()
    assert observed == [("invalid", 2)]
    assert selections == [True]
    assert calls == [(7, 42, 2)]


@pytest.mark.parametrize("kind,action", _MODAL_ACTIONS)
def test_confirmed_management_action_keeps_its_original_api_target(management, monkeypatch, kind, action):
    _context, page, _notices = management(kind)
    calls = []

    class API:
        def __init__(self, **_kwargs):
            pass

        def __getattr__(self, method):
            return lambda *args: calls.append((method, args))

    api_names = {
        "files": "ParatranzFilesAPI",
        "members": "ParatranzMembersAPI",
        "terms": "ParatranzTermsAPI",
        "overview": "ParatranzProjectAPI",
    }
    monkeypatch.setattr(_PAGES[kind][0], api_names[kind], API)
    QTimer.singleShot(0, _accept_modal)
    getattr(page, action)()
    operation = page._workers[-1]
    operation.call()
    assert len(calls) == 1
    assert calls[0][1][0] == 7
    if action in ("_delete_file", "_remove_member", "_edit_term", "_delete_term"):
        assert calls[0][1][1] == 11


@pytest.mark.parametrize("kind", ("files", "members", "terms"))
def test_current_read_failure_explains_refresh_and_can_retry(management, kind):
    _context, page, notices = management(kind)
    old = page._workers[-1]
    old.error.emit("synthetic read failure")
    assert "synthetic read failure" in notices[-1][-1]
    assert "刷新" in notices[-1][-1]
    QTest.mouseClick(page._refresh_btn, Qt.MouseButton.LeftButton)
    retry = page._workers[-1]
    assert retry is not old
    notices.clear()
    old.error.emit("late failure")
    retry.result.emit(_DATA[kind])
    assert page._table.rowCount() == 1
    assert not notices


@pytest.mark.parametrize("kind,action", _MODAL_ACTIONS)
def test_returning_to_original_project_does_not_revive_an_old_operation(management, kind, action):
    context, page, notices = management(kind)
    QTimer.singleShot(0, _accept_modal)
    getattr(page, action)()
    operation = page._workers[-1]
    context.current_project = {"id": 8, "name": "Other project"}
    context.current_project = {"id": 7, "name": "Current project"}
    before = len(page._workers)
    operation.result.emit({"id": 7, "name": "Stale edit"})
    operation.error.emit("stale failure")
    assert context.current_project["name"] == "Current project"
    assert len(page._workers) == before
    assert not notices
