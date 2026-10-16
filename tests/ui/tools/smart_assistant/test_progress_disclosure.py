"""Compact request cards retain actionable exceptions and explicit detail disclosure."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPlainTextEdit
import pytest

from tests.ui.tools.smart_assistant.test_conversation_layout import _request
from transbridge.application.assistant_requests.models import AssistantExecutionRef, EffectIntent, ExecutionDispatch
from transbridge.ui.tools.smart_assistant.request_progress_view import RequestProgressCard
from transbridge.ui.tools.smart_assistant.submission_scope import SubmissionScope
from transbridge.ui.tools.smart_assistant.theme_support import QUIET_BUTTON_STYLE, SmartAssistantTheme

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def cards():
    widgets = []
    font = Path("C:/Windows/Fonts/msyh.ttc")
    font_id = QFontDatabase.addApplicationFont(str(font)) if font.exists() else -1

    def create(request=None):
        events = []
        card = RequestProgressCard(
            request or _request(),
            control=lambda rid, command: events.append((rid, command)),
            timeline=lambda rid: events.append((rid, "timeline")),
            undo=lambda: events.append(("r", "undo")),
            locate=lambda ids: events.append((ids, "locate")),
            review=lambda rid, failures: events.append((rid, "failures" if failures else "review")),
            theme=SmartAssistantTheme(),
        )
        card.setFixedWidth(420)
        card.show()
        card.adjustSize()
        _APP.processEvents()
        widgets.append(card)
        return SimpleNamespace(card=card, events=events)

    yield create
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    _APP.processEvents()
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


def _click(widget):
    assert widget.isVisible()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    _APP.processEvents()


def _completed(request):
    return replace(request, status="completed", items=tuple(replace(i, status="satisfied") for i in request.items))


def test_running_card_discloses_goal_scope_and_real_controls_only_on_request(cards):
    case = cards()
    card = case.card
    card.scope_view.set_scope(SubmissionScope("提交时：原项目 · 选中 2 条", "原始选区：entry-a、entry-b"))
    card.show_undo("仅撤销本任务已经核验的 2 条修改", True)
    card.adjustSize()
    compact_height = card.height()
    assert compact_height < 100
    assert card.status.isVisible() and card.progress.isVisible() and card.expand.isVisible()
    assert not card.attention.isVisible() and not card.review.isVisible()
    for child in (card.goal, card.scope_view, card.items, card.pause, card.cancel, card.locate, card.undo_button):
        assert not child.isVisible()

    _click(card.expand)
    card.adjustSize()
    assert card.height() > compact_height + 70
    assert card.goal.text() == card.request.goal
    assert card.scope_view.summary.isVisible()
    assert card.items.isVisible() and card.undo_message.isVisible()
    for child in (card.pause, card.cancel, card.locate, card.undo_button):
        _click(child)
    assert case.events == [("r", "pause"), ("r", "cancel"), (("message-a",), "locate"), ("r", "undo")]
    _click(card.scope_view.details_button)
    detail = card.scope_view.findChild(QPlainTextEdit)
    assert detail.isVisible() and "entry-a、entry-b" in detail.toPlainText()
    detail.window().close()
    assert card.expand.styleSheet() == card.pause.styleSheet() == QUIET_BUTTON_STYLE


def test_user_disclosure_survives_refresh_and_terminal_transition(cards):
    card = cards().card
    _click(card.expand)
    card.update_request(replace(card.request, revision=2))
    card.set_tasks(())
    assert card.expand.isChecked() and card.details.isVisible()
    card.update_request(_completed(card.request))
    assert card.expand.isChecked() and card.details.isVisible()
    _click(card.expand)
    card.update_request(card.request)
    card.set_tasks(())
    assert not card.expand.isChecked() and not card.details.isVisible()
    assert card.status.text() == "已完成 2/2 项"
    assert not card.progress.isVisible() and not card.attention.isVisible()


def test_completed_card_keeps_only_summary_result_and_optional_details(cards):
    case = cards(_completed(_request()))
    card = case.card
    assert card.height() < 75
    assert card.status.isVisible() and card.review.isVisible()
    assert not card.details.isVisible() and not card.attention.isVisible()
    _click(card.review)
    assert case.events == [("r", "review")]
    card.show_undo("缺少可核验的撤销记录", False)
    _click(card.expand)
    assert card.undo_message.isVisible() and not card.undo_button.isVisible()


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("item_status", ["pending", "waiting", "running"])
def test_confirmation_remains_visible_when_details_are_closed(cards, paused, item_status):
    request = _request("waiting-task")
    request = replace(
        request,
        pause_reasons=("user_paused",) if paused else (),
        items=(replace(request.items[0], status=item_status, waiting_reasons=("approval_revalidation",)),),
    )
    case = cards(request)
    assert not case.card.details.isVisible()
    assert case.card.notice.isVisible() and "确认" in case.card.notice.text()
    assert case.card.attention_action.text() == "查看待确认操作"
    _click(case.card.attention_action)
    assert case.events == [("waiting-task", "resume")]


@pytest.mark.parametrize("kind", ["effect", "dispatch"])
def test_unknown_result_requires_reconciliation_without_opening_details(cards, kind):
    request = _completed(_request("uncertain-task"))
    execution = AssistantExecutionRef(request.request_id, 1, ("translate",), "attempt", "dispatch", "turn")
    if kind == "effect":
        request = replace(request, effects=(EffectIntent("e", "hash", execution, 1, status="outcome_unknown"),))
    else:
        request = replace(
            request, dispatches=(ExecutionDispatch("d", execution, "job", "run", status="outcome_unknown"),)
        )
    case = cards(request)
    assert "待核对" in case.card.status.text()
    assert case.card.notice.isVisible() and "尚未核实" in case.card.notice.text()
    assert not case.card.details.isVisible() and not case.card.resume.isVisible()
    _click(case.card.attention_action)
    assert case.events == [("uncertain-task", "reconcile")]


def test_failure_and_pause_keep_their_recovery_actions_visible(cards):
    request = _request("failed-task")
    case = cards(replace(request, status="failed", items=(replace(request.items[0], status="failed"),)))
    assert case.card.notice.isVisible() and "失败原因" in case.card.notice.text()
    assert not case.card.details.isVisible()
    _click(case.card.failures)
    _click(case.card.review)
    assert case.events == [("failed-task", "failures"), ("failed-task", "review")]
    case.card.update_request(replace(request, pause_reasons=("user_paused",)))
    assert case.card.notice.isVisible() and "已暂停" in case.card.notice.text()
    assert not case.card.failures.isVisible()
    _click(case.card.resume)
    assert case.events[-1] == ("failed-task", "resume")


def test_multiple_background_jobs_remain_bounded_but_full_progress_is_available(cards):
    card = cards().card
    tasks = [
        {
            "task_id": f"job-{index}",
            "status": "running",
            "metadata": {"assistant_request_id": "r"},
            "progress": {"current": index, "total": 24, "phase": "translating"},
        }
        for index in range(10)
    ]
    card.set_tasks(tasks)
    card.adjustSize()
    assert "0/24" in card.status.text() and "另有 9 项" in card.status.text()
    assert "9/24" not in card.status.text()
    assert card.height() < 100
    _click(card.expand)
    assert card.background.isVisible() and "9/24" in card.background.text()
