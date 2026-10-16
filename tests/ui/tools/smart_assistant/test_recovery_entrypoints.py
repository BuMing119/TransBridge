"""Visible failure/cancellation/context recovery without reopening request details."""

from dataclasses import replace

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.smart_assistant import test_progress_disclosure as disclosure_tests
from tests.ui.tools.smart_assistant.test_conversation_layout import _request
from transbridge.ui.tools.smart_assistant.request_list_view import RequestListView

cards = disclosure_tests.cards


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_recovery_is_explicit_full_restart_without_opening_details(cards, status):
    request = replace(_request(), status=status, items=tuple(replace(item, status=status) for item in _request().items))
    case = cards(request)
    assert not case.card.details.isVisible()
    assert case.card.attention_action.isVisible() and case.card.attention_action.text() == "重新处理"
    QTest.mouseClick(case.card.attention_action, Qt.MouseButton.LeftButton)
    assert case.events == [(request.request_id, "restart")]


def test_context_wait_displays_real_reason_and_retry_preserves_separate_pause(cards):
    case = cards(replace(_request(), pause_reasons=("user_paused",)))
    case.card.set_context_wait({"code": "CONTEXT_INVALID", "message": "摘要文件校验失败，原始资料已保留。"})
    assert "摘要文件校验失败" in case.card.notice.text()
    assert "容量" not in case.card.notice.text()
    assert case.card.resume.isVisible()
    assert case.card.attention_action.text() == "重试上下文"
    QTest.mouseClick(case.card.attention_action, Qt.MouseButton.LeftButton)
    assert case.events == [(case.card.request.request_id, "retry_context")]


def test_list_recovery_buttons_and_structural_error_wording_are_reachable():
    view = RequestListView()
    events = []
    view.control.connect(lambda rid, command: events.append((rid, command)))
    request = _request()
    try:
        view.context_waits = {request.request_id: {"message": "上下文来源无法核验"}}
        view.display((request,))
        view.show()
        QApplication.processEvents()
        assert view.controls["retry_context"].isVisible()
        assert "来源无法核验" in view.items.item(0).text()
        view.set_tasks(())
        assert "来源无法核验" in view.items.item(0).text()
        QTest.mouseClick(view.controls["retry_context"], Qt.MouseButton.LeftButton)
        view.display((replace(request, status="cancelled"),))
        assert view.controls["restart"].isVisible()
        QTest.mouseClick(view.controls["restart"], Qt.MouseButton.LeftButton)
        view.set_pending(1, replanning=1)
        assert "未接纳" in view.pending.text() and "选择" not in view.pending.text()
        assert events == [(request.request_id, "retry_context"), (request.request_id, "restart")]
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
