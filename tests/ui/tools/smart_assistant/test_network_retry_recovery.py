"""Retry buttons resume durable requests after provider timeouts."""

import pytest

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import (
    _requests,
    _until,
    environment,
)
from transbridge.smart_assistant.request_protocol import ROUTING_TOOL
from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

__all__ = ["environment"]


@pytest.mark.parametrize("stage", ["routing", "execution"])
@pytest.mark.parametrize("resume_from_list", [False, True])
def test_timeout_retry_button_resumes_original_work(environment, monkeypatch, stage, resume_from_list):
    chat = environment.panel.chat
    client = environment.client
    if stage == "execution":
        client.tool_goal = "Recover this request"
    original = client.chat_stream_with_tools
    failed = []

    def respond(messages, max_tokens, tools, *args, **kwargs):
        routing = ROUTING_TOOL in {tool.name for tool in tools}
        should_fail = routing if stage == "routing" else bool(tools) and not routing and client.tool_issued
        if should_fail and not failed:
            failed.append(True)
            raise TimeoutError("network timeout")
        return original(messages, max_tokens, tools, *args, **kwargs)

    monkeypatch.setattr(client, "chat_stream_with_tools", respond)
    chat.send_user_message("Recover this request")
    if stage == "execution":
        _until(lambda: any(isinstance(widget, ToolCard) for widget in chat._message_list._owned_widgets))
        card = next(widget for widget in chat._message_list._owned_widgets if isinstance(widget, ToolCard))
        card._exec_btn.click()
    _until(lambda: failed and chat._retry_btn is not None and environment.binding.admission is None)
    before = _requests(environment)
    if stage == "execution":
        assert before[0].pause_reasons
        assert not before[0].terminal
    if resume_from_list:
        if stage == "execution":
            environment.binding.view.control.emit(before[0].request_id, "resume")
        else:
            environment.binding.view.retry_input.emit()
    else:
        chat._retry_btn.click()
    _until(lambda: _requests(environment) and _requests(environment)[0].terminal)
    requests = _requests(environment)
    assert len(requests) == 1
    if before:
        assert requests[0].request_id == before[0].request_id
    assert len(environment.service.state(environment.binding.context)["ingress"]) == 1
    if stage == "execution":
        results = [
            message
            for message in chat._conversation.get_transcript()
            if message.get("role") == "tool" and message.get("name") == "get_statistics"
        ]
        assert len(results) == 1
        assert "Offline statistics" in results[0]["content"]
    # A second click on the old button cannot restart completed work.
    chat._retry_btn.click()
    assert _requests(environment) == requests
