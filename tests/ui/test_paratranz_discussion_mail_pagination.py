"""Offline discussion and mail pagination through the existing Qt workers."""

from types import SimpleNamespace

from tests.ui.responsive_layout_support import themed_layout_app as themed_layout_app
from tests.ui.test_paratranz_secondary_state import views as views
from transbridge.ui.paratranz import issues_tab, mails_dialog


def test_discussion_uses_page_count_and_resets_when_status_changes(views, monkeypatch):  # noqa: F811 - imported fixture
    context, make, _notices = views
    view = make("issues")
    calls = []

    def list_issues(project_id, *, status, page, page_size):
        calls.append((project_id, status, page, page_size))
        return {"page": page, "pageCount": 2, "results": [{"id": page, "title": f"Page {page}"}]}

    monkeypatch.setattr(
        issues_tab, "ParatranzIssuesAPI", lambda **kwargs: SimpleNamespace(list_issues=list_issues, close=lambda: None)
    )
    context.select({"id": 5})
    first = view._workers[-1]
    first.result.emit(first.callback())
    assert view._next_btn.isEnabled()
    assert not view._previous_btn.isEnabled()

    view._next_btn.click()
    second = view._workers[-1]
    second.result.emit(second.callback())
    assert view._list.item(0).text() == "Page 2"
    assert view._previous_btn.isEnabled()
    assert not view._next_btn.isEnabled()

    view.load_issues()
    stale = view._workers[-1]
    stale_payload = stale.callback()
    view._tab_bar.setCurrentIndex(1)
    closed = view._workers[-1]
    closed.result.emit(closed.callback())
    stale.result.emit(stale_payload)

    assert view._list.item(0).text() == "Page 1"
    assert calls == [(5, 0, 1, 50), (5, 0, 2, 50), (5, 0, 2, 50), (5, 1, 1, 50)]


def test_inbox_loads_additional_contacts_without_resetting_active_draft(views, monkeypatch):  # noqa: F811
    _context, make, notices = views
    calls = []

    def list_mails(*, page, page_size):
        calls.append((page, page_size))
        entries = {
            1: [{"uid": 10, "user": {"nickname": "Alice"}, "createdAt": "2", "unreadCount": 1}],
            2: [{"uid": 20, "user": {"nickname": "Bob"}, "createdAt": "1", "unreadCount": 0, "status": 0}],
        }
        return {"page": page, "pageCount": 2, "results": entries[page]}

    monkeypatch.setattr(
        mails_dialog, "ParatranzMailsAPI", lambda **kwargs: SimpleNamespace(list_mails=list_mails, close=lambda: None)
    )
    view = make("mails")
    first = view._workers[-1]
    first.result.emit(first.callback())
    assert view._more_mails_btn.isEnabled()
    assert view._conv_list.item(0).text().startswith("[未读] Alice")
    view._conv_list.setCurrentRow(0)
    view._send_input.setPlainText("Unsent draft")
    view._more_mails_btn.click()
    view._workers[-1].error.emit("offline")
    assert notices and view._more_mails_btn.isEnabled()
    assert view._conv_list.count() == 1

    view._more_mails_btn.click()
    second = view._workers[-1]
    second.result.emit(second.callback())

    assert view._workers[-1] is second
    assert view._conv_list.count() == 2
    assert view._conv_list.item(1).text().startswith("Bob")
    assert view._current_uid == 10
    assert view._send_input.toPlainText() == "Unsent draft"
    assert not view._more_mails_btn.isEnabled()
    assert calls == [(1, 100), (2, 100)]


def _message(message_id):
    return {"id": message_id, "from": 10, "content": f"Message {message_id}", "createdAt": f"{message_id:02}"}


def test_conversation_retries_same_cursor_and_preserves_messages(views, monkeypatch):  # noqa: F811
    _context, make, _notices = views
    calls = []

    def get_conversation(uid, *, before, limit):
        calls.append((uid, before, limit))
        if before is None:
            return [_message(message_id) for message_id in range(52, 2, -1)]
        return [_message(3), _message(2), _message(1)]

    monkeypatch.setattr(
        mails_dialog,
        "ParatranzMailsAPI",
        lambda **kwargs: SimpleNamespace(get_conversation=get_conversation, close=lambda: None),
    )
    view = make("mails")
    view._workers[-1].result.emit([{"uid": 10}])
    view._conv_list.setCurrentRow(0)
    latest = view._workers[-1]
    latest.result.emit(latest.callback())
    assert view._more_messages_btn.isEnabled()
    view._more_messages_btn.click()
    view._workers[-1].error.emit("offline")
    assert view._retry_btn.isVisible()
    assert view._msg_layout.count() == 51

    view._retry_btn.click()
    older = view._workers[-1]
    older.result.emit(older.callback())

    assert view._msg_layout.count() == 53
    first = view._msg_layout.itemAt(0).widget().layout().itemAt(0).widget()
    assert first.text().startswith("Message 1\n")
    assert not view._more_messages_btn.isEnabled()
    assert calls == [(10, None, 50), (10, 3, 50)]

    view._load_conversation(10)
    view._workers[-1].result.emit([_message(message_id) for message_id in range(53, 3, -1)])
    assert view._msg_layout.count() == 54
    assert not view._more_messages_btn.isEnabled()


def test_conversation_ignores_an_older_page_after_switching_recipient(views):  # noqa: F811
    _context, make, _notices = views
    view = make("mails")
    view._workers[-1].result.emit([{"uid": 10, "createdAt": "2"}, {"uid": 20, "createdAt": "1"}])
    view._conv_list.setCurrentRow(0)
    view._workers[-1].result.emit([_message(message_id) for message_id in range(3, 53)])
    view._more_messages_btn.click()
    stale = view._workers[-1]

    view._conv_list.setCurrentRow(1)
    view._workers[-1].result.emit([])
    stale.result.emit([_message(1)])

    assert view._current_uid == 20
    assert view._msg_layout.count() == 1
    assert not view._more_messages_btn.isEnabled()
