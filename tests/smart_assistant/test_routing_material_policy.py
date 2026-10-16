"""Attachment instructions apply only to material actually present in the current input."""

import json

import pytest

from transbridge.application.assistant_requests.attachments import MATERIAL_POLICY, encode_user_content
from transbridge.application.assistant_requests.routing import RoutingBatch, RoutingSource
from transbridge.smart_assistant.request_router import routing_messages


@pytest.mark.parametrize(
    ("source_id", "attachments", "has_material"),
    [
        ("current", [], False),
        ("current", [{"name": "guide.txt", "format": "text", "content": "Use consistent terms."}], True),
        ("previous", [{"name": "guide.txt", "format": "text", "content": "Use consistent terms."}], False),
    ],
)
def test_routing_material_policy_preserves_plain_input_prompt(source_id, attachments, has_material):
    batch = RoutingBatch("batch", "session", (RoutingSource("current", "Please help"),))
    baseline = routing_messages(batch, ())
    history = [{"role": "user", "message_id": source_id, "content": encode_user_content("Please help", attachments)}]
    messages = routing_messages(batch, (), history=history)
    assert ("Current input attachments" in messages[0]["content"]) is has_material
    if not has_material:
        assert messages[0] == baseline[0]
    entry = json.loads(messages[1]["content"])["inputs"][0]
    assert entry["text"] == "Please help"
    if source_id == "current":
        assert entry["attachments"] == attachments
        assert entry["material_only"] is True
        assert entry["attachment_policy"] == MATERIAL_POLICY
    else:
        assert "attachments" not in entry
