"""Undo limitations reach the user before automatic execution or confirmation."""

from types import SimpleNamespace

import pytest

from transbridge.smart_assistant.session_controller import SessionController
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.undo_notices import undo_notices


@pytest.mark.parametrize("mode", ["automatic", "tool", "plan", "restored"])
def test_limitation_precedes_execution_or_confirmation(monkeypatch, mode):
    spec = SimpleNamespace(permission="write", display_name="上传", require_confirmation=False)
    monkeypatch.setattr(ToolRegistry, "get", lambda _: spec)
    events = []
    controller = SessionController(
        on_system_message=lambda message: events.append(("notice", message)),
        on_present_tool_card=lambda _: events.append(("card", "")),
        on_present_plan_card=lambda _: events.append(("card", "")),
        on_execute_react_async=lambda _: events.append(("execute", "")) or True,
    )
    parsed = {"mode": "plan" if mode == "plan" else "react", "steps": [{"tool": "upload_entries", "args": {}}]}
    if mode == "restored":
        controller.restore_confirmation(parsed)
    else:
        controller.auto_mode = mode == "automatic"
        controller.handle_user_message("upload")
        controller.handle_llm_response(parsed)
    assert events[0][0] == "notice" and "远端" in events[0][1]
    assert events[1][0] == ("execute" if mode == "automatic" else "card")


def test_read_tools_and_supported_writes_do_not_get_spurious_undo_notices(monkeypatch):
    specs = {
        "get_statistics": SimpleNamespace(permission="read", display_name="统计"),
        "edit_translation": SimpleNamespace(permission="write", display_name="修改译文"),
    }
    monkeypatch.setattr(ToolRegistry, "get", specs.get)
    assert undo_notices([{"tool": name} for name in specs]) == []
