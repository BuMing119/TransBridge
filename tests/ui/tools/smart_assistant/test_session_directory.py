"""Directory reads leave Qt responsive and never authorize Session activation."""

from pathlib import Path
from threading import Event
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.smart_assistant.test_session_workspace_navigation import _until, workspace
from transbridge.ui.tools.smart_assistant.session_directory import SessionDirectory

workspace = workspace
_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def directory():
    value = SessionDirectory()
    yield value
    value.close()
    value.deleteLater()
    _APP.processEvents()


def _row(name="A", project="project-a", variant="variant-a"):
    return {"session_id": name, "name": name, "project_id": project, "variant_id": variant}


@pytest.mark.parametrize("block_labels", [False, True])
def test_cold_directory_and_labels_leave_qt_timer_responsive(directory, block_labels):
    entered, release = Event(), Event()
    threads, installed, ticks = [], [], []

    def hold():
        threads.append(QThread.currentThread())
        entered.set()
        assert release.wait(5)

    def rows():
        if not block_labels:
            hold()
        return [_row()]

    def labels(project, variant):
        if block_labels:
            hold()
        return {"project_name": "工程 A", "variant_name": "稳定版", "available": True}

    directory.updated.connect(installed.append)
    try:
        directory.refresh(SimpleNamespace(list_sessions=rows), labels)
        _until(entered.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        _until(lambda: ticks)
        assert not installed and not release.is_set()
        assert all(thread is not _APP.thread() for thread in threads)
    finally:
        release.set()
    _until(lambda: installed)
    assert installed[0][0]["project_name"] == "工程 A"


def test_refreshes_coalesce_and_stale_snapshot_never_replaces_current_rows(directory):
    entered, release = Event(), Event()
    calls, installed = [], []

    def old_rows():
        calls.append("old")
        entered.set()
        assert release.wait(5)
        return [_row("old")]

    def newest_rows():
        calls.append("newest")
        return [_row("newest")]

    directory.updated.connect(installed.append)
    try:
        directory.refresh(SimpleNamespace(list_sessions=old_rows))
        _until(entered.is_set)
        for _ in range(10):
            directory.refresh(SimpleNamespace(list_sessions=lambda: pytest.fail("Superseded refresh ran")))
        directory.refresh(SimpleNamespace(list_sessions=newest_rows))
    finally:
        release.set()
    _until(lambda: installed)
    assert calls == ["old", "newest"]
    assert [[row["session_id"] for row in rows] for rows in installed] == [["newest"]]


def test_failure_keeps_previous_snapshot_and_surfaces_error(directory):
    installed, errors = [], []
    directory.updated.connect(installed.append)
    directory.failed.connect(errors.append)
    directory.refresh(SimpleNamespace(list_sessions=lambda: [_row()]))
    _until(lambda: installed)

    def unreadable():
        raise OSError("directory unavailable")

    directory.refresh(SimpleNamespace(list_sessions=unreadable))
    _until(lambda: errors)
    assert len(installed) == 1 and directory.rows[0]["session_id"] == "A"
    assert str(errors[0]) == "directory unavailable"


def test_closing_and_destroying_during_read_cannot_deliver_to_qobject():
    directory = SessionDirectory()
    entered, release, finished = Event(), Event(), Event()
    delivered = []

    def rows():
        entered.set()
        assert release.wait(5)
        finished.set()
        return [_row()]

    directory.updated.connect(delivered.append)
    directory.failed.connect(delivered.append)
    try:
        directory.refresh(SimpleNamespace(list_sessions=rows))
        _until(entered.is_set)
        directory.close()
        directory.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        release.set()
    _until(finished.is_set)
    _APP.processEvents()
    assert not delivered


def test_navigation_reads_one_record_without_scanning_directory(workspace, monkeypatch):
    case = workspace
    _until(lambda: not case.panel._sessions.directory.pending)
    scans = []
    original = case.commands.list_sessions

    def list_sessions():
        scans.append(QThread.currentThread())
        return original()

    monkeypatch.setattr(case.commands, "list_sessions", list_sessions)
    case.preview("B")
    _until(lambda: not case.panel._sessions.directory.pending)
    assert scans and all(thread is not _APP.thread() for thread in scans)
    assert case.panel._sessions.preview


def test_refresh_keeps_old_rows_and_uses_current_selection_and_workbench(workspace, monkeypatch):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    entered, release = Event(), Event()
    original = case.commands.list_sessions
    previous = list(panel._session_list._session_data)

    def rows():
        snapshot = original()
        entered.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr(case.commands, "list_sessions", rows)
    try:
        panel._refresh_session_list()
        _until(entered.is_set)
        assert panel._session_list._session_data == previous
        case.preview("B")
        case.context.active_version_identity = ("project-b", "variant-b")
        panel._workspace.changed()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _until(lambda: ticks)
    finally:
        release.set()
    _until(lambda: not panel._sessions.directory.pending)
    assert panel._session_list._active_sid == case.refs["B"].identity.value
    assert panel._session_list._workbench_project_id == "project-b"
    assert panel._sessions.values["session_id"] == case.refs["B"].identity.value


def test_restore_failure_never_creates_a_replacement_conversation(workspace, monkeypatch):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    panel._active_session_id = None
    panel._sessions.values = {}
    creations = []
    monkeypatch.setattr(panel._sessions, "create", creations.append)

    def unreadable():
        raise OSError("cannot read catalog")

    monkeypatch.setattr(case.commands, "list_sessions", unreadable)
    panel._sessions.restore()
    _until(lambda: bool(panel.chat.notices))
    assert not creations and panel._current_session_id() is None
    assert "cannot read catalog" in panel.chat.notices[-1]


def test_unavailable_record_does_not_prevent_starting_a_new_conversation(workspace, monkeypatch):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    panel._active_session_id = None
    panel._sessions.values = {}
    creations = []
    monkeypatch.setattr(panel._sessions, "create", creations.append)
    rows = [{**row, "available": False} for row in case.commands.list_sessions()]
    monkeypatch.setattr(case.commands, "list_sessions", lambda: rows)

    panel._sessions.restore()

    _until(lambda: bool(creations))
    assert creations == [""]
    assert not panel.chat.notices


def test_banner_and_execution_confirmation_use_installed_labels_without_gui_io(workspace, monkeypatch):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    entered, release = Event(), Event()
    threads = []

    def labels(project, variant):
        threads.append(QThread.currentThread())
        entered.set()
        assert release.wait(5)
        return {"project_name": project, "variant_name": variant, "available": True}

    panel._navigation["session_project_labels"] = labels
    monkeypatch.setattr(panel._workspace, "confirm_switch", lambda *_: False)
    try:
        panel._refresh_session_list()
        _until(entered.is_set)
        case.preview("B")
        panel._workspace.refresh_banner()
        assert not panel._workspace.ensure("发送", lambda: pytest.fail("Must not execute preview"))
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _until(lambda: ticks)
    finally:
        release.set()
    _until(lambda: not panel._sessions.directory.pending)
    assert threads and all(thread is not _APP.thread() for thread in threads)


@pytest.mark.parametrize("cold", [True, False])
def test_record_scope_reads_after_cold_load_or_file_change_run_off_qt(workspace, monkeypatch, cold):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    if cold:
        case.commands._scope_cache._entries.clear()
    else:
        record = Path(case.repository.path_for(case.refs["B"]))
        # Whitespace changes the file signature without changing the valid record.
        record.write_bytes(record.read_bytes() + b"\n")
    entered, release = Event(), Event()
    original = case.repository.read_snapshot
    read_threads = []

    def read(*args):
        read_threads.append(QThread.currentThread())
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(case.repository, "read_snapshot", read)
    try:
        panel._refresh_session_list()
        _until(entered.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _until(lambda: ticks)
        assert not release.is_set()
    finally:
        release.set()
    _until(lambda: not panel._sessions.directory.pending)
    assert read_threads and all(thread is not _APP.thread() for thread in read_threads)


def test_restoration_waits_until_workbench_installation_then_matches_new_scope(workspace):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    panel._active_session_id = None
    panel._sessions.values = {}
    case.context.project_opening = True
    panel._sessions.restore()
    _until(lambda: not panel._sessions.directory.pending)
    assert panel._current_session_id() is None
    case.context.active_version_identity = ("project-b", "variant-b")
    case.context.project_opening = False
    panel._workspace.changed()
    _until(lambda: panel._workspace.ready())
    assert panel._current_session_id() == case.refs["B"].identity.value
    assert case.lifecycle.active.aggregate.ref == case.refs["B"]


def test_directory_delivery_cannot_replace_explicitly_created_session(workspace, monkeypatch):
    case = workspace
    panel = case.panel
    _until(lambda: not panel._sessions.directory.pending)
    panel._active_session_id = None
    panel._sessions.values = {}
    entered, release = Event(), Event()
    original = case.commands.list_sessions

    def rows():
        snapshot = original()
        entered.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr(case.commands, "list_sessions", rows)
    try:
        panel._sessions.restore()
        _until(entered.is_set)
        panel._sessions.create("Newer user choice")
        chosen = panel._current_session_id()
        assert chosen is not None and chosen not in {ref.identity.value for ref in case.refs.values()}
    finally:
        release.set()
    _until(lambda: not panel._sessions.directory.pending)
    assert panel._current_session_id() == chosen
    assert panel._session_list._active_sid == chosen
    assert panel._sessions.values["name"] == "Newer user choice"


def test_uncached_labels_explain_loading_and_preserve_invalid_record_reason(directory):
    value = _row()
    pending = directory.labels(value, lookup_available=True)
    assert pending["project_name"] == "project-a" and not pending["available"]
    assert "仍在加载" in pending["reason"]
    installed = []
    directory.updated.connect(installed.append)
    directory.refresh(
        SimpleNamespace(list_sessions=lambda: [value]),
        lambda *_: {
            "project_name": "A",
            "variant_name": "Missing variant",
            "available": False,
            "reason": "此对话绑定的版本已不存在。",
        },
    )
    _until(lambda: installed)
    assert directory.labels(value, lookup_available=True)["reason"] == "此对话绑定的版本已不存在。"
