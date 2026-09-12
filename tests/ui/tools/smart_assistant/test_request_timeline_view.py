"""Timeline reads stay separate from lifecycle commands and Qt remains responsive."""

from copy import deepcopy
from threading import Event
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from transbridge.ui.tools.smart_assistant.request_list_view import RequestListView
from transbridge.ui.tools.smart_assistant.request_timeline_view import RequestTimelineView, format_event

_APP = QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = monotonic() + 3
    while not predicate() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate()


def _event(sequence, **values):
    return {
        "event_id": f"internal-event-{sequence}",
        "sequence": sequence,
        "timestamp": "2026-09-12T10:00:00Z",
        "session_id": "internal-session",
        "operation": "request.pause",
        "origin": "user",
        "request_ids": ["internal-request"],
        "before": {"requests": [{"status": "open", "pause_reasons": []}]},
        "after": {"requests": [{"status": "open", "pause_reasons": ["user_paused"]}]},
        "references": {"message_ids": ["internal-message"]},
        "details": {},
        **values,
    }


def test_formatter_exposes_state_and_waiting_reason_without_diagnostic_ids():
    event = _event(1, after={"status": "waiting", "waiting_reasons": ["approval"]})
    result = format_event(event)
    assert "用户操作" in result
    assert "已暂停推进" in result
    assert "待完成 → 等待处理、等待批准" in result
    assert "internal-" not in result


def test_formatter_reads_journal_item_deltas_and_handles_wait_operations():
    event = _event(
        1,
        operation="request.wait",
        origin="runtime",
        before={"items": {"private-item": {"status": "running", "waiting_reasons": []}}},
        after={"items": {"private-item": {"status": "waiting", "waiting_reasons": ["approval"]}}},
    )
    result = format_event(event)
    assert "请求进入等待" in result
    assert "执行中 → 等待处理、等待批准" in result
    assert "private-item" not in result
    assert "已解除请求等待" in format_event({**event, "operation": "request.unblock"})


def test_dialog_pages_refreshes_and_reveals_only_explicit_plain_text_diagnostics():
    calls = []
    injected = '<a href="https://example.invalid">unsafe</a>'

    def load(sequence):
        calls.append(sequence)
        events = [_event(sequence + 1, details={"raw": injected})] if sequence < 2 else []
        return {"events": events, "next_sequence": min(sequence + 1, 2), "has_more": sequence < 1}

    dialog = RequestTimelineView(load)
    dialog.show()
    try:
        _until(lambda: dialog.events.count() == 1)
        assert dialog.details.isHidden()
        assert "internal-" not in dialog.events.item(0).text()
        dialog.diagnostics.click()
        assert not dialog.details.isHidden()
        assert injected.replace('"', '\\"') in dialog.details.toPlainText()
        assert dialog.details.isReadOnly()
        assert dialog.status.textFormat() == Qt.TextFormat.PlainText
        dialog.more.click()
        _until(lambda: dialog.events.count() == 2)
        assert dialog.more.text() == "刷新后续记录"
        dialog.more.click()
        _until(lambda: not dialog._busy)
        assert dialog.events.count() == 2
        assert calls == [0, 1, 2]
    finally:
        dialog.close()


def test_old_session_without_events_is_explicitly_identified():
    dialog = RequestTimelineView(lambda _: {"events": [], "next_sequence": 0, "has_more": False})
    try:
        _until(lambda: not dialog._busy)
        assert "旧会话可能没有日志" in dialog.status.text()
        assert "不会从聊天内容补推历史" in dialog.status.text()
    finally:
        dialog.close()


def test_read_failure_can_retry_without_changing_cursor(caplog):
    calls = []

    def load(sequence):
        calls.append(sequence)
        if len(calls) == 1:
            raise OSError("injected storage error")
        return {"events": [_event(1)], "next_sequence": 1, "has_more": False}

    dialog = RequestTimelineView(load)
    try:
        _until(lambda: not dialog._busy)
        assert "无法读取已保存的过程" in dialog.status.text()
        assert "injected storage error" in caplog.text
        assert dialog.more.text() == "重试读取"
        dialog.more.click()
        _until(lambda: dialog.events.count() == 1)
        assert calls == [0, 0]
    finally:
        dialog.close()


def test_slow_read_does_not_block_qt_or_dialog_close():
    started, release, finished = Event(), Event(), Event()

    def load(_sequence):
        started.set()
        try:
            assert release.wait(3)
            return {"events": [], "next_sequence": 0, "has_more": False}
        finally:
            finished.set()

    dialog = RequestTimelineView(load)
    dialog.show()
    try:
        _until(started.is_set)
        assert dialog._busy
        dialog.close()
        assert dialog._closed
        _APP.processEvents()
        release.set()
        _until(finished.is_set)
        _APP.processEvents()
    finally:
        release.set()


def test_timeline_buttons_are_independent_from_request_control():
    view = RequestListView()
    controls, timeline = [], []
    view.control.connect(lambda *args: controls.append(args))
    view.timeline.connect(timeline.append)
    view.timeline_session.click()
    assert timeline == [""]
    assert not view.timeline_request.isEnabled()
    request = SimpleNamespace(
        request_id="request-a", goal="translate", status="open", items=[], effects=[], dispatches=[], pause_reasons=[]
    )
    view.display([request])
    view.timeline_request.click()
    assert timeline == ["", "request-a"]
    assert controls == []
    view.close()


def test_management_uses_captured_session_read_only(monkeypatch):
    from transbridge.application.assistant_requests import journal
    from transbridge.ui.tools.smart_assistant.request_management_binding import RequestManagementBinding

    context = SimpleNamespace(session_id="original-session")
    state = {"requests": [], "journal": [_event(1)]}
    original = deepcopy(state)
    reads = []

    def read_state(read_context):
        reads.append(read_context.session_id)
        return state

    def read_events(value, **kwargs):
        assert value is state
        assert kwargs == {"request_id": "request-a", "after_sequence": 0, "limit": 100}
        return {"events": value["journal"], "next_sequence": 1, "has_more": False}

    monkeypatch.setattr(journal, "read_events", read_events)
    # Intentionally no wake/interrupt/fail/command methods: viewing must never call them.
    binding = SimpleNamespace(context=context, service=SimpleNamespace(state=read_state), facade=None)
    management = RequestManagementBinding(binding)
    management.show_timeline("request-a")
    dialog = next(iter(management._timelines))
    binding.context = SimpleNamespace(session_id="other-session")
    try:
        _until(lambda: dialog.events.count() == 1)
        assert reads == ["original-session"]
        assert state == original
    finally:
        dialog.close()
    assert not management._timelines


def test_session_timeline_stays_reachable_without_requests_or_pending_inputs():
    from transbridge.ui.tools.smart_assistant.request_management_binding import RequestManagementBinding

    view = RequestListView()
    view.hide()
    state = {"lifecycle_events": [_event(1, operation="routing.rejected", request_ids=[])]}
    binding = SimpleNamespace(
        context=SimpleNamespace(session_id="original-session"),
        service=SimpleNamespace(state=lambda _: state, requests=lambda _: ()),
        facade=None,
        view=view,
    )
    management = RequestManagementBinding(binding)
    view.timeline.connect(management.show_timeline)
    management.refresh()
    assert not view.isHidden()
    assert not view.timeline_request.isEnabled()
    view.timeline_session.click()
    dialog = next(iter(management._timelines))
    try:
        _until(lambda: dialog.events.count() == 1)
        assert "请求归属未通过校验" in dialog.events.item(0).text()
    finally:
        dialog.close()
        view.close()
