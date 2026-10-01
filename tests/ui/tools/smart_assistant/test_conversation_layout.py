"""Real Qt layout, identity, composer and request-projection regressions."""

from dataclasses import replace
import os
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QFontDatabase, QKeyEvent, QPalette
from PyQt6.QtWidgets import QApplication, QMenu

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from tests.ui.tools.smart_assistant.test_theme_migration import _snapshot
from transbridge.application.assistant_requests.models import ItemStatus, RequestItem, RequestStatus, UserRequest
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.tools.smart_assistant.message_bubble import MessageBubble
from transbridge.ui.tools.smart_assistant.quick_actions import QuickActionsChips
from transbridge.ui.tools.smart_assistant.request_progress_view import RequestProgressCard
from transbridge.ui.tools.smart_assistant.theme_support import SmartAssistantTheme

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _request(identity="r", source="message-a", status=RequestStatus.OPEN):
    return UserRequest(
        identity,
        "s",
        "翻译任务对白，保持人物称呼一致",
        (
            RequestItem("translate", "翻译选中的对白", status=ItemStatus.RUNNING),
            RequestItem("check", "检查人物称呼的一致性"),
        ),
        source_message_ids=(source,),
        status=status,
    )


def test_primary_action_stops_running_turn_and_keyboard_can_submit_followup(environment, monkeypatch):
    chat = environment.panel.chat
    ui = chat._input_view
    stops, sends = [], []
    monkeypatch.setattr(environment.binding.management, "stop_generation", lambda: stops.append(True))
    ui.set_running(True)
    assert chat._send_btn.isEnabled()
    assert chat._send_btn.text() == "停止"
    assert not chat._send_btn.icon().isNull()
    chat._send_btn.click()
    assert stops == [True]
    monkeypatch.setattr(chat, "send_user_message", sends.append)
    chat.set_input("补充要求")
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert chat.eventFilter(chat._input, event)
    assert sends == ["补充要求"]
    ui.set_running(False)
    assert chat._send_btn.text() == "发送"
    assert not chat._send_btn.isEnabled()


def test_progress_anchors_use_message_identity_and_survive_incremental_answers(environment):
    chat = environment.panel.chat
    chat._message_list.clear()
    for identity in ("message-a", "message-b"):
        bubble = MessageBubble("同样的要求", "user", theme=chat._theme)
        bubble.setProperty("message_id", identity)
        chat._message_list.add_bubble(bubble)
    presenter = chat._presentation.progress
    presenter.display((_request(), _request("r2", "message-b")))
    a, b = presenter.cards.values()
    layout = chat._msg_layout
    users = chat._message_list._owned_widgets
    assert layout.indexOf(users[0]) < layout.indexOf(a) < layout.indexOf(users[1]) < layout.indexOf(b)
    answer = MessageBubble("完成后会在这里呈现结果", "assistant", theme=chat._theme)
    chat._message_list.add_bubble(answer)
    assert layout.indexOf(users[1]) < layout.indexOf(answer) < layout.indexOf(b)
    a.expand.click()
    presenter.display((_request(), _request("r2", "message-b")))
    assert presenter.cards["r"] is a and a.expand.isChecked()
    chat._message_list.clear()
    assert a.isHidden() and b.isHidden()
    presenter.display(())
    assert not presenter.cards
    assert not chat._message_list._attachments


def test_restore_history_keeps_source_ids_for_progress(environment):
    chat = environment.panel.chat
    chat._message_list.clear()
    chat.load_history([
        {"role": "user", "content": "第一个任务", "message_id": "message-a"},
        {"role": "assistant", "content": "已收到"},
        {"role": "user", "content": "第二个任务", "message_id": "message-b"},
    ])
    chat._presentation.progress.display((_request(),))
    card = chat._presentation.progress.cards["r"]
    assert chat._msg_layout.indexOf(card) < chat._msg_layout.indexOf(chat._message_list._owned_widgets[-1])


def test_context_uses_live_project_projection_and_same_selection_as_submission(environment):
    chat = environment.panel.chat
    chat._ctx.project_name = "示例项目"
    chat._ctx.selected_ids = {"entry-a", "entry-b"}
    chat._presentation.refresh()
    assert "示例项目" in chat._presentation.context_label.full_text
    assert "选中 2 条" in chat._presentation.context_label.full_text
    assert set(environment.binding._selection()["selected_entry_ids"]) == {"entry-a", "entry-b"}
    chat._ctx.selected_ids.clear()
    chat._presentation.refresh()
    assert "未选中" in chat._presentation.context_label.full_text


def test_management_is_optional_and_narrow_composer_remains_reachable(environment, monkeypatch):
    panel = environment.panel
    chat = panel.chat
    panel.setFloating(True)
    panel.resize(1024, 760)
    panel.show()
    _until(lambda: chat._presentation.empty.isVisible())
    assert chat._presentation.dialog.isHidden()
    assert chat._main_layout.indexOf(environment.binding.view) == -1
    panel.resize(480, 620)
    _until(lambda: panel._session_list._collapsed)
    assert chat._send_btn.isVisible()
    _until(lambda: chat.mapTo(panel, QPoint()).x() < 100)
    assert chat._send_btn.geometry().right() <= chat._send_btn.parentWidget().width()
    selected = []
    monkeypatch.setattr(chat._input_view, "_set_input", selected.append)

    def choose_export(menu, *_):
        next(action for action in menu.actions() if action.text() == "导出").trigger()

    monkeypatch.setattr(QMenu, "exec", choose_export)
    chat._input_view._more_button.click()
    assert selected == ["请导出当前集合为 JSON"]
    chat._presentation.show_details()
    assert chat._presentation.dialog.isVisible()
    chat._presentation.dialog.close()
    panel.resize(1024, 760)
    _until(lambda: not panel._session_list._collapsed)


def test_progress_reports_items_and_only_authoritative_undo(environment):
    presenter = environment.panel.chat._presentation.progress
    request = _request()
    presenter.display((request,))
    card = presenter.cards["r"]
    assert "0 / 2 项事项完成" in card.status.text()
    assert card.undo_button.isHidden()
    environment.binding.undo._target = (environment.binding.context, 0, "r")
    presenter.show_undo("已核验", True)
    assert not card.undo_button.isHidden()
    presenter.show_undo("缺少可核验记录", False)
    assert card.undo_button.isHidden()
    assert "缺少" in card.undo_message.text()
    completed = replace(
        request,
        status=RequestStatus.COMPLETED,
        items=tuple(replace(item, status=ItemStatus.SATISFIED) for item in request.items),
    )
    presenter.display((completed,))
    assert "已完成" in card.status.text()
    assert card.progress.isHidden()
    assert card.resume.isHidden()


def test_capture_conversation_layout(environment):
    """Optional actual-widget screenshots; no real model, project or user data."""
    panel, binding = environment.panel, environment.binding
    binding.set_active(False)
    destination = os.environ.get("TB_ASSISTANT_CAPTURE_DIR")
    if destination:
        font_path = Path("C:/Windows/Fonts/msyh.ttc")
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    chat = panel.chat
    chat._ctx.project_name = "示例项目 / 任务对白"
    chat._ctx.selected_ids = set(map(str, range(24)))
    panel.setFloating(True)
    panel.resize(1024, 760)
    panel.show()
    binding.set_active(False)
    chat._message_list.clear()
    chat.load_history([
        {"role": "user", "content": "翻译这 24 条对白，保持人物称呼一致。", "message_id": "message-a"},
        {"role": "assistant", "content": "我会参考项目术语表翻译这些对白，并检查人物称呼的一致性。"},
    ])
    request = replace(_request(), session_id=binding.context.session_id)
    environment.service.transact(binding.context, lambda state: state.update(requests=[request.to_dict()]))
    binding.refresh()
    _until(lambda: "r" in chat._presentation.progress.cards)
    chat._controller._state = chat._controller.State.EXECUTING
    chat._presentation.refresh()
    QApplication.processEvents()
    assert chat.findChildren(RequestProgressCard)
    if destination:
        target = Path(destination)
        assert target.is_dir()
        assert panel.grab().save(str(target / "assistant-desktop.png"))
        panel.resize(480, 620)
        _until(lambda: chat.mapTo(panel, QPoint()).x() < 100)
        for _ in range(5):
            QApplication.processEvents()
        assert panel.grab().save(str(target / "assistant-narrow.png"))
        completed = replace(
            request,
            status=RequestStatus.COMPLETED,
            items=tuple(replace(item, status=ItemStatus.SATISFIED) for item in request.items),
        )
        environment.service.transact(binding.context, lambda state: state.update(requests=[completed.to_dict()]))
        binding.refresh()
        _until(lambda: "已完成" in chat._presentation.progress.cards["r"].status.text())
        panel.resize(1024, 760)
        chat._controller._state = chat._controller.State.IDLE
        chat._presentation.refresh()
        QApplication.processEvents()
        assert panel.grab().save(str(target / "assistant-completed.png"))
    assert chat._presentation.dialog.isHidden()


def test_switch_session_removes_previous_request_cards(environment):
    chat = environment.panel.chat
    chat._presentation.progress.display((_request(),))
    assert chat._presentation.progress.cards
    environment.panel._on_create_session("另一段对话")
    _until(lambda: not chat._presentation.progress.cards)
    assert not chat._message_list._attachments


def test_clear_keeps_old_requests_in_details_without_restoring_inline_cards(environment):
    chat, binding = environment.panel.chat, environment.binding
    request = replace(_request(status=RequestStatus.COMPLETED), session_id=binding.context.session_id)
    environment.service.transact(binding.context, lambda state: state.update(requests=[request.to_dict()]))
    chat.load_history([{"role": "user", "content": "old", "message_id": "message-a"}])
    binding.refresh()
    _until(lambda: "r" in chat._presentation.progress.cards)
    card = chat._presentation.progress.cards["r"]
    assert chat._message_list.contains(card)
    chat._clear_conversation()
    binding.refresh()
    _until(lambda: not binding.management._refresh._pending)
    chat._presentation.refresh()
    assert card.isHidden() and not chat._message_list.contains(card)
    assert not chat._presentation.empty.isHidden()
    assert [r.request_id for r in binding.view._requests] == ["r"]
    chat.load_history([{"role": "user", "content": "new", "message_id": "message-b"}])
    chat._presentation.progress.display((request, _request("r2", "message-b")))
    assert card.isHidden()
    assert chat._message_list.contains(chat._presentation.progress.cards["r2"])


def test_history_limit_hides_orphaned_progress_before_and_after_refresh(environment):
    chat = environment.panel.chat
    chat._message_list.clear()
    chat.load_history([{"role": "user", "content": "old", "message_id": "message-a"}])
    presenter = chat._presentation.progress
    presenter.display((_request(),))
    card = presenter.cards["r"]
    chat.load_history([{"role": "assistant", "content": f"answer {i}"} for i in range(100)])
    assert len(chat._message_list._owned_widgets) == 100
    assert card.isHidden() and not chat._message_list.contains(card)
    presenter.display((_request(),))
    assert card.isHidden() and not chat._message_list.contains(card)


def test_progress_received_before_history_waits_for_matching_source(environment):
    chat = environment.panel.chat
    chat._message_list.clear()
    request = replace(_request(), source_message_ids=("message-a", "message-b"))
    presenter = chat._presentation.progress
    presenter.display((request,))
    card = presenter.cards["r"]
    assert card.isHidden() and not chat._message_list.contains(card)
    chat.load_history([{"role": "user", "content": "source", "message_id": "message-b"}])
    assert not card.isHidden() and chat._message_list.contains(card)
    assert chat._msg_layout.indexOf(card) > chat._msg_layout.indexOf(chat._message_list._owned_widgets[-1])


def test_empty_quick_actions_follow_theme_round_trip(environment):
    chat = environment.panel.chat
    theme = SmartAssistantTheme()
    for revision, scheme in enumerate((ThemeScheme.LIGHT, ThemeScheme.DARK, ThemeScheme.LIGHT), 1):
        theme.update(_snapshot(scheme, revision))
        chat.apply_theme(theme)
        reference = QuickActionsChips(theme=theme)
        try:
            for actual, expected in zip(chat._presentation.quick._buttons, reference._buttons, strict=True):
                for role in (QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button):
                    assert actual.palette().color(role) == expected.palette().color(role)
            assert chat._presentation.quick._overflow.palette() == reference._overflow.palette()
        finally:
            reference.deleteLater()
