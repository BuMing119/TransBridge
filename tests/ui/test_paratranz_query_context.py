"""Queries and modal edits stay attached to the selected cloud project and page."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.paratranz import strings_tab


class _Context(QObject):
    project_selected = pyqtSignal(object)
    paratranz_permissions_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="synthetic")
        self.current_project = None

    def select(self, project):
        self.current_project = project
        self.project_selected.emit(project)

    def is_admin(self):
        return True


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, call):
        super().__init__()
        self.call = call

    def start(self):
        pass


_FILES = [{"id": 10, "name": "First.json"}, {"id": 20, "name": "Second.json"}]
_ROWS = [{"id": 1, "key": "first", "original": "Original", "translation": "Saved", "stage": 0}]
_REAL_WARNING = QMessageBox.warning


@pytest.fixture
def view(layout_app, monkeypatch):
    monkeypatch.setattr(strings_tab, "ApiWorker", _Worker)
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: notices.append(_args))
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: notices.append(_args))
    context = _Context()
    widget = strings_tab.StringsTab(context)
    context.select({"id": 7})
    widget.resize(520, 440)
    widget.show()
    settle_layout(layout_app)
    yield context, widget, notices
    widget.close()
    widget.deleteLater()
    settle_layout(layout_app)


def _loaded(widget):
    widget._workers[-2].result.emit(_FILES)
    widget._workers[-1].result.emit(_ROWS)


@pytest.mark.parametrize("choice", ("retry", "cancel", "switch-project"))
def test_current_file_filter_failure_offers_a_safe_retry(view, layout_app, monkeypatch, choice):
    context, widget, _notices = view
    monkeypatch.setattr(QMessageBox, "warning", _REAL_WARNING)
    failed_worker = widget._workers[0]
    widget._workers[1].result.emit(_ROWS)
    before = len(widget._workers)
    seen, errors = [], []

    def answer():
        box = QApplication.activeModalWidget()
        try:
            assert isinstance(box, QMessageBox)
            assert "synthetic file failure" in box.text()
            assert "重试" in box.text()
            assert box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel)
            seen.append(True)
            if choice == "switch-project":
                context.select({"id": 8})
            button = QMessageBox.StandardButton.Cancel if choice == "cancel" else QMessageBox.StandardButton.Retry
            QTest.mouseClick(box.button(button), Qt.MouseButton.LeftButton)
        except BaseException as error:
            errors.append(error)
            if isinstance(box, QMessageBox):
                box.reject()

    QTimer.singleShot(0, answer)
    failed_worker.error.emit("synthetic file failure")
    if errors:
        raise errors[0]
    assert seen == [True]
    assert len(widget._workers) == before + {"retry": 1, "cancel": 0, "switch-project": 2}[choice]
    if choice == "retry":
        widget._workers[-1].result.emit(_FILES)
        assert widget._file_combo.count() == 3
    if choice != "switch-project":
        assert widget._strings == _ROWS


@pytest.mark.parametrize("change", ("clear", "project", "new-request"))
def test_stale_file_filter_failure_never_shows_a_warning(view, change):
    context, widget, notices = view
    old = widget._workers[0]
    if change == "clear":
        context.select(None)
    elif change == "project":
        context.select({"id": 8})
    else:
        widget._load_files_for_filter()
    count = len(widget._workers)
    old.error.emit("old file failure")
    assert notices == []
    assert len(widget._workers) == count


def test_cleared_project_cannot_be_repopulated_by_late_results(view):
    context, widget, notices = view
    files, rows = widget._workers
    context.select(None)
    files.result.emit(_FILES)
    rows.result.emit(_ROWS)
    rows.error.emit("old project failed")
    assert widget._strings == []
    assert widget._files == []
    assert widget._table.rowCount() == 0
    assert widget._file_combo.count() == 1
    assert not widget._create_btn.isEnabled()
    assert not widget._batch_stage_btn.isEnabled()
    assert notices == []


def test_pending_and_failed_query_clears_old_rows_and_can_retry(view):
    _context, widget, notices = view
    _loaded(widget)
    widget._table.selectRow(0)
    widget._page_spin.setValue(2)
    widget.load_strings()
    assert widget._table.rowCount() == 0
    assert not widget._batch_stage_btn.isEnabled()
    worker = widget._workers[-1]
    worker.error.emit("synthetic timeout")
    assert "synthetic timeout" in widget._query_status.text()
    assert not notices
    widget._query_btn.click()
    worker.error.emit("late timeout")
    assert "late timeout" not in widget._query_status.text()
    widget._workers[-1].result.emit([])
    assert widget._table.rowCount() == 0
    assert "2" in widget._query_status.text()
    assert "没有" in widget._query_status.text()


def test_query_keeps_the_existing_api_filter_and_paging_contract(view, monkeypatch):
    _context, widget, _notices = view
    _loaded(widget)
    widget._file_combo.setCurrentIndex(1)
    widget._stage_combo.setCurrentIndex(widget._stage_combo.findData(1))
    widget._page_spin.setValue(3)
    widget._page_size_combo.setCurrentIndex(widget._page_size_combo.findData(100))
    requests = []

    def list_strings(project_id, **options):
        requests.append((project_id, options))
        return _ROWS

    monkeypatch.setattr(
        strings_tab, "ParatranzStringsAPI", lambda **_kwargs: SimpleNamespace(list_strings=list_strings)
    )
    widget._query_btn.click()
    worker = widget._workers[-1]
    worker.result.emit(worker.call())
    assert requests == [(7, {"page": 3, "page_size": 100, "file": 10, "stage": 1})]
    assert widget._table.rowCount() == 1
    assert "3" in widget._query_status.text()


def test_details_use_the_loaded_query_when_pending_filter_controls_change(view, monkeypatch, layout_app):
    _context, widget, _notices = view
    _loaded(widget)
    widget._file_combo.setCurrentIndex(1)
    widget._page_spin.setValue(2)
    widget.load_strings()
    widget._workers[-1].result.emit(_ROWS)
    # The controls are a new query draft; they have not been submitted yet.
    widget._file_combo.setCurrentIndex(2)
    widget._page_spin.setValue(5)
    opened = []

    class Detail:
        def __init__(self, *_args, **kwargs):
            opened.append(kwargs)
            self.strings_updated = SimpleNamespace(connect=lambda _callback: None)

        def exec(self):
            return 0

        def was_modified(self):
            return False

    monkeypatch.setattr(strings_tab, "StringDetailDialog", Detail)
    settle_layout(layout_app)
    point = widget._table.visualItemRect(widget._table.item(0, 0)).center()
    QTest.mouseClick(widget._table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(widget._table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert len(opened) == 1
    assert opened[0]["file_id"] == 10
    assert opened[0]["page"] == 2


@pytest.mark.parametrize("action", ("create", "stage", "delete"))
def test_project_switch_during_a_modal_action_does_not_submit_to_the_new_project(view, action):
    context, widget, _notices = view
    _loaded(widget)
    widget._table.selectRow(0)
    before = len(widget._workers)
    errors = []

    def switch_and_accept():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog is not None
            context.select({"id": 8})
            if isinstance(dialog, QMessageBox):
                dialog.button(QMessageBox.StandardButton.Yes).click()
            else:
                dialog.accept()
        except BaseException as error:
            errors.append(error)
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, switch_and_accept)
    {"create": widget._create_string, "stage": widget._batch_set_stage, "delete": widget._batch_delete}[action]()
    if errors:
        raise errors[0]
    # Only the new project's file and string reads were submitted.
    assert len(widget._workers) == before + 2
    assert widget._project_id == 8
    assert "项目已切换" in widget._query_status.text()


@pytest.mark.parametrize("action", ("create", "stage", "delete"))
def test_confirmed_operations_keep_the_target_and_ignore_late_callbacks_in_another_project(view, action, monkeypatch):
    context, widget, notices = view
    _loaded(widget)
    widget._table.selectRow(0)
    calls = []
    api = SimpleNamespace(
        create_string=lambda *args, **kwargs: calls.append(("create", args, kwargs)),
        batch_strings=lambda *args, **kwargs: calls.append(("batch", args, kwargs)),
    )
    monkeypatch.setattr(strings_tab, "ParatranzStringsAPI", lambda **_kwargs: api)

    def accept():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            dialog.button(QMessageBox.StandardButton.Yes).click()
        else:
            if action == "create":
                dialog._key.setText("new-key")
                dialog._original.setText("New original")
                dialog._file_combo.setCurrentIndex(1)
            dialog.accept()

    QTimer.singleShot(0, accept)
    {"create": widget._create_string, "stage": widget._batch_set_stage, "delete": widget._batch_delete}[action]()
    worker = widget._workers[-1]
    worker.call()
    assert len(calls) == 1
    assert calls[0][1][0] == 7
    if action == "create":
        assert calls[0][1][1]["file"] == 10
        assert calls[0][1][1]["key"] == "new-key"
    else:
        assert calls[0][1][1:] == ("delete" if action == "delete" else "update", [1])
    context.select({"id": 8})
    count = len(widget._workers)
    worker.result.emit(None)
    worker.error.emit("late operation failure")
    assert len(widget._workers) == count
    assert notices == []
