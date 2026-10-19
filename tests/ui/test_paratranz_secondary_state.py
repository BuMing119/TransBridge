"""Secondary ParaTranz views isolate late responses from the current context."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox, QPushButton
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.paratranz import export_tab, issues_tab, mails_dialog


class _Context(QObject):
    project_selected = pyqtSignal(object)
    paratranz_permissions_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(token="fixture")
        self.current_user = {"id": 1}
        self.current_project = None

    def is_admin(self):
        return True

    def is_member(self):
        return True

    def select(self, project):
        self.current_project = project
        self.project_selected.emit(project)


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, callback, **_kwargs):
        super().__init__()
        self.callback = callback

    def start(self):
        pass


@pytest.fixture
def views(layout_app, monkeypatch):
    for module in (mails_dialog, issues_tab, export_tab):
        monkeypatch.setattr(module, "ApiWorker", _Worker)
    notices = []
    for method in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, method, lambda *args: notices.append(args[1:]))
    context = _Context()
    made = []

    def make(kind):
        classes = {"mails": mails_dialog.MailsDialog, "issues": issues_tab.IssuesTab, "export": export_tab.ExportTab}
        widget = classes[kind](context)
        made.append(widget)
        widget.resize(820, 560)
        widget.show()
        settle_layout(layout_app)
        return widget

    yield context, make, notices
    for widget in made:
        widget.close()
        widget.deleteLater()
    settle_layout(layout_app)


def _mail_contacts(view):
    view._workers[-1].result.emit([
        {"from": 10, "to": 1, "fromUser": {"nickname": "Alice"}, "createdAt": "2"},
        {"from": 20, "to": 1, "fromUser": {"nickname": "Bob"}, "createdAt": "1"},
    ])


def _click_list(view, row):
    item = view.item(row)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualItemRect(item).center())


def _issue_list(view):
    view._workers[-1].result.emit([{"id": 10, "title": "First"}, {"id": 20, "title": "Second"}])


def test_mail_contact_selection_clears_old_messages_and_keeps_drafts_per_recipient(views):
    _context, make, _notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    view._workers[-1].result.emit([{"from": 10, "content": "Alice message"}])
    view._send_input.setPlainText("Draft for Alice")

    QTest.keyClick(view._conv_list, Qt.Key.Key_Down)

    assert view._current_uid == 20
    assert view._msg_layout.count() == 1
    assert view._send_input.toPlainText() == ""
    view._send_input.setPlainText("Draft for Bob")
    QTest.keyClick(view._conv_list, Qt.Key.Key_Up)
    assert view._current_uid == 10
    assert view._send_input.toPlainText() == "Draft for Alice"


def test_new_mail_invalidates_old_conversation_response(views, monkeypatch):
    _context, make, _notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    old = view._workers[-1]
    monkeypatch.setattr(mails_dialog.QInputDialog, "getText", lambda *args: ("30", True))

    view._new_mail()
    old.result.emit([{"from": 10, "content": "Alice message"}])

    assert view._current_uid == 30
    assert view._msg_layout.count() == 1


def test_mail_send_completion_cannot_clear_or_reload_another_recipient(views):
    _context, make, _notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    view._send_input.setPlainText("Send to Alice")
    view._send_btn.click()
    sending = view._workers[-1]
    count = len(view._workers)
    view._send_btn.click()
    assert len(view._workers) == count
    _click_list(view._conv_list, 1)
    view._send_input.setPlainText("Bob's new draft")
    count = len(view._workers)

    sending.result.emit({})

    assert view._send_input.toPlainText() == "Bob's new draft"
    assert view._current_uid == 20
    assert len(view._workers) == count


def test_mail_load_error_is_visible_and_can_retry_current_recipient(views):
    _context, make, _notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    view._workers[-1].error.emit("fixture offline")
    assert "fixture offline" in view._conversation_status.text()
    assert view._retry_btn.isVisible()
    view._retry_btn.click()
    view._workers[-1].result.emit([])
    assert "暂无消息" in view._conversation_status.text()
    assert not view._retry_btn.isVisible()


@pytest.mark.parametrize("focused", ["_send_input", "_conv_list"])
def test_enter_does_not_send_a_mail_from_editor_or_contact_list(views, focused):
    _context, make, _notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    view._send_input.setPlainText("Unsent draft")
    count = len(view._workers)

    QTest.keyClick(getattr(view, focused), Qt.Key.Key_Return)

    assert len(view._workers) == count
    assert "Unsent draft" in view._send_input.toPlainText()


def test_mail_failure_retains_draft_and_retry_uses_original_recipient(views, monkeypatch):
    _context, make, notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    view._send_input.setPlainText("  Original draft  ")
    view._send_btn.click()
    view._workers[-1].error.emit("fixture failure")
    assert notices and "Alice" in notices[-1][-1]
    assert view._send_btn.isEnabled()
    assert view._send_input.toPlainText() == "  Original draft  "
    view._send_btn.click()
    sending = view._workers[-1]
    _click_list(view._conv_list, 1)
    calls = []
    monkeypatch.setattr(
        mails_dialog, "ParatranzMailsAPI", lambda **kwargs: SimpleNamespace(send_mail=lambda *args: calls.append(args))
    )

    sending.callback()

    assert calls == [(10, "Original draft")]


def test_mail_completion_preserves_text_typed_while_sending(views):
    _context, make, _notices = views
    view = make("mails")
    _mail_contacts(view)
    _click_list(view._conv_list, 0)
    view._send_input.setPlainText("First message")
    view._send_btn.click()
    sending = view._workers[-1]
    view._send_input.setPlainText("Next message")

    sending.result.emit({})

    assert view._send_input.toPlainText() == "Next message"
    assert view._send_btn.isEnabled()


@pytest.mark.parametrize("project", [{"id": 2}, None])
def test_issue_project_change_clears_target_draft_and_ignores_late_details(views, project):
    context, make, _notices = views
    view = make("issues")
    context.select({"id": 1})
    _issue_list(view)
    _click_list(view._list, 0)
    old = view._workers[-1]
    view._reply_input.setPlainText("Project one draft")

    context.select(project)
    old.result.emit({"activities": [{"content": "Project one response"}]})

    assert view._current_issue is None
    assert not view._reply_input.toPlainText()
    assert not view._replies_view.toPlainText()
    assert not view._reply_btn.isEnabled()


def test_issue_keyboard_selection_and_late_reply_preserve_new_draft(views):
    context, make, _notices = views
    view = make("issues")
    context.select({"id": 1})
    _issue_list(view)
    _click_list(view._list, 0)
    view._reply_input.setPlainText("Reply to first")
    view._reply_btn.click()
    sending = view._workers[-1]
    count = len(view._workers)
    view._reply_btn.click()
    assert len(view._workers) == count

    QTest.keyClick(view._list, Qt.Key.Key_Down)
    view._reply_input.setPlainText("Reply to second")
    sending.result.emit({})

    assert view._current_issue["id"] == 20
    assert view._reply_input.toPlainText() == "Reply to second"


def test_issue_close_completion_does_not_change_new_selection(views):
    context, make, _notices = views
    view = make("issues")
    context.select({"id": 1})
    _issue_list(view)
    _click_list(view._list, 0)
    view._close_btn.click()
    closing = view._workers[-1]
    _click_list(view._list, 1)

    closing.result.emit({})

    assert view._current_issue["id"] == 20
    assert view._current_issue.get("status", 0) == 0
    assert view._close_btn.text() == "关闭讨论"


def test_issue_failure_is_visible_and_retry_keeps_current_target(views):
    context, make, _notices = views
    view = make("issues")
    context.select({"id": 1})
    _issue_list(view)
    _click_list(view._list, 0)
    view._workers[-1].error.emit("fixture offline")
    assert "fixture offline" in view._replies_view.toPlainText()
    assert view._detail_retry.isVisible()
    view._detail_retry.click()
    view._workers[-1].result.emit({"activities": []})
    assert "暂无回复" in view._replies_view.toPlainText()
    assert not view._detail_retry.isVisible()


def test_issue_reply_failure_retains_draft_and_captured_project_target(views, monkeypatch):
    context, make, notices = views
    view = make("issues")
    context.select({"id": 1})
    _issue_list(view)
    _click_list(view._list, 0)
    view._reply_input.setPlainText("  Reply text  ")
    view._reply_btn.click()
    view._workers[-1].error.emit("fixture failure")
    assert notices
    assert view._reply_btn.isEnabled()
    assert view._reply_input.toPlainText() == "  Reply text  "
    view._reply_btn.click()
    sending = view._workers[-1]
    context.select({"id": 2})
    calls = []
    monkeypatch.setattr(
        issues_tab, "ParatranzIssuesAPI", lambda **kwargs: SimpleNamespace(reply_issue=lambda *args: calls.append(args))
    )

    sending.callback()
    sending.result.emit({})

    assert calls == [(1, 10, "Reply text")]
    assert view._current_issue is None


def test_issue_refresh_failure_does_not_leave_a_stale_action_target(views):
    context, make, _notices = views
    view = make("issues")
    context.select({"id": 1})
    _issue_list(view)
    _click_list(view._list, 0)
    view.load_issues()
    view._workers[-1].error.emit("fixture failure")

    assert "fixture failure" in view._list_status.text()
    assert view._current_issue is None
    assert not view._reply_btn.isEnabled()
    assert not view._close_btn.isEnabled()


def test_export_clearing_project_rejects_late_metadata(views):
    context, make, _notices = views
    view = make("export")
    context.select({"id": 1})
    old = view._workers[-1]
    context.select(None)

    old.result.emit({"total": 999})

    assert view._lbl_total.text() == "—"
    assert not view._download_btn.isEnabled()


@pytest.mark.parametrize("operation", ["_trigger_export", "_download_artifacts"])
def test_export_old_operation_does_not_change_new_project_state(views, monkeypatch, operation):
    context, make, notices = views
    view = make("export")
    monkeypatch.setattr(export_tab.QFileDialog, "getSaveFileName", lambda *args: ("fixture.zip", ""))
    context.select({"id": 1})
    getattr(view, operation)()
    old = view._workers[-1]
    context.select({"id": 2})
    view._workers[-1].result.emit({"total": 2})
    expected = view._status_lbl.text()
    assert not view._busy
    assert view._download_btn.isEnabled()

    old.progress.emit(0, 0, "Old project progress")
    old.result.emit("old-project.zip")
    old.error.emit("Old project error")

    assert view._status_lbl.text() == expected
    assert view._lbl_total.text() == "2"
    assert not notices


def test_export_metadata_failure_has_non_destructive_retry(views):
    context, make, _notices = views
    view = make("export")
    context.select({"id": 1})
    view._workers[-1].error.emit("fixture offline")
    assert "fixture offline" in view._info_status.text()
    assert view._refresh_btn.isEnabled()
    view._refresh_btn.click()
    view._workers[-1].result.emit({"total": 2})
    assert "fixture offline" not in view._info_status.text()
    assert view._lbl_total.text() == "2"
    view._refresh_btn.click()
    view._workers[-1].result.emit(None)
    assert view._lbl_total.text() == "—"
    assert "暂无导出记录" in view._info_status.text()


@pytest.mark.parametrize("operation", ["_trigger_export", "_download_artifacts"])
def test_export_failure_restores_actions_and_retains_captured_api_target(views, monkeypatch, operation):
    context, make, _notices = views
    view = make("export")
    monkeypatch.setattr(export_tab.QFileDialog, "getSaveFileName", lambda *args: ("fixture.zip", ""))
    context.select({"id": 1})
    getattr(view, operation)()
    view._workers[-1].error.emit("fixture failure")
    assert not view._busy
    assert view._trigger_btn.isEnabled() and view._download_btn.isEnabled()
    getattr(view, operation)()
    pending = view._workers[-1]
    context.select({"id": 2})
    calls = []
    if operation == "_trigger_export":
        monkeypatch.setattr(
            export_tab,
            "ArtifactWorkflow",
            lambda config: SimpleNamespace(trigger_and_download=lambda *args, **kwargs: calls.append(args)),
        )
    else:
        monkeypatch.setattr(
            export_tab,
            "ParatranzExportAPI",
            lambda **kwargs: SimpleNamespace(download_artifacts=lambda *args: calls.append(args)),
        )

    pending.callback()

    assert calls == [(1, "fixture.zip")]


@pytest.mark.parametrize("kind,width", [("mails", 720), ("issues", 720), ("export", 460)])
@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize("point_size", [9, 14])
def test_secondary_actions_remain_visible_in_narrow_themed_views(views, layout_app, kind, width, mode, point_size):
    _context, make, _notices = views
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    view = make(kind)
    apply_layout_font(view, service.snapshot(), point_size)
    view.resize(width, 600)
    settle_layout(layout_app)
    try:
        assert view.width() <= width
        for button in view.findChildren(QPushButton):
            if button.isVisible():
                assert button.font().pointSizeF() == point_size
                assert button.width() >= button.minimumSizeHint().width()
                assert button.height() >= button.fontMetrics().height()
                assert view.rect().contains(button.mapTo(view, button.rect().center()))
    finally:
        service.close()
